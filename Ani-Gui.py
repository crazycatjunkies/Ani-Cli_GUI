import customtkinter as ctk
from PIL import Image
import subprocess
import threading
import requests
import io
import sys
import os
import json
import time
import hashlib

# --- Configuration ---
ANI_CLI_PATH = "ani-cli"
THUMBNAIL_SIZE = (160, 225)  # Adjusted for a 3-column layout
ANI_CACHE_DIR = "Ani-Cache"
os.makedirs(ANI_CACHE_DIR, exist_ok=True)
# --- End Configuration ---

# Lock for Jikan rate limiting
jikan_lock = threading.Lock()
last_jikan_time = [0]  # mutable list to store last request time


class AniAPI:
    """
    Handles direct interaction with the AllAnime GraphQL API,
    replicating the search and episode list functions of ani-cli.
    """
    def __init__(self):
        self.api_url = "https://api.allanime.day/api"
        self.api_refr = "https://allmanga.to"
        self.agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0"
        self.headers = {"User-Agent": self.agent, "Referer": self.api_refr}
        
        self.search_gql = '''
        query($search: SearchInput, $limit: Int, $page: Int, $translationType: VaildTranslationTypeEnumType, $countryOrigin: VaildCountryOriginEnumType) {
          shows(search: $search, limit: $limit, page: $page, translationType: $translationType, countryOrigin: $countryOrigin) {
            edges {
              _id
              name
              availableEpisodes
              __typename
            }
          }
        }
        '''
        self.episodes_list_gql = '''
        query ($showId: String!) {
          show(_id: $showId) {
            _id
            availableEpisodesDetail
          }
        }
        '''

    def search(self, query, mode='sub'):
        variables = {
            "search": {"allowAdult": False, "allowUnknown": False, "query": query},
            "limit": 40, "page": 1, "translationType": mode, "countryOrigin": "ALL"
        }
        params = {"variables": json.dumps(variables), "query": self.search_gql}
        
        response = requests.get(self.api_url, params=params, headers=self.headers)
        response.raise_for_status()
        data = response.json()['data']['shows']['edges']
        
        results = []
        for index, item in enumerate(data):
            results.append({
                "index": index + 1,
                "id": item['_id'],
                "title": item['name'],
                "episodes": item['availableEpisodes'].get(mode, 0)
            })
        return results

    def get_episodes(self, show_id, mode='sub'):
        variables = {"showId": show_id}
        params = {"variables": json.dumps(variables), "query": self.episodes_list_gql}
        
        response = requests.get(self.api_url, params=params, headers=self.headers)
        response.raise_for_status()
        
        episodes_data = response.json()['data']['show']['availableEpisodesDetail'].get(mode, [])
        return sorted(episodes_data, key=float)


class AniCliGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.api = AniAPI()
        self.title("ani-cli GUI")
        self.geometry("1100x750")

        self.anime_list = []
        self.selected_anime_id = None
        self.selected_anime_index = None
        self.selected_anime_title = None
        self.selected_episode = None
        self.last_query = ""
        self.thumbnail_cache = {}
        self.placeholder_image = self._create_placeholder_image()

        self._setup_ui()

    def _create_placeholder_image(self):
        image = Image.new('RGB', THUMBNAIL_SIZE, (50, 50, 50))
        return ctk.CTkImage(light_image=image, dark_image=image, size=THUMBNAIL_SIZE)

    def _get_cache_base(self, title):
        """Generate base cache path from title hash."""
        safe_name = hashlib.md5(title.encode("utf-8")).hexdigest()
        return os.path.join(ANI_CACHE_DIR, safe_name)

    def _setup_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        
        # --- Top Frame ---
        top_frame = ctk.CTkFrame(self)
        top_frame.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        top_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(top_frame, text="Search Anime:").grid(row=0, column=0, padx=5, pady=5)
        self.search_entry = ctk.CTkEntry(top_frame, placeholder_text="e.g., Solo Leveling")
        self.search_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.search_entry.bind("<Return>", self.search_anime)
        self.search_button = ctk.CTkButton(top_frame, text="Search", command=self.search_anime)
        self.search_button.grid(row=0, column=2, padx=5, pady=5)
        
        # --- Middle Frame ---
        middle_frame = ctk.CTkFrame(self)
        middle_frame.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="nsew")
        middle_frame.grid_columnconfigure(0, weight=3)
        middle_frame.grid_columnconfigure(1, weight=1)
        middle_frame.grid_rowconfigure(0, weight=1)

        self.anime_results_frame = ctk.CTkScrollableFrame(middle_frame, label_text="Search Results")
        self.anime_results_frame.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        self.anime_results_frame.grid_columnconfigure((0, 1, 2), weight=1)

        right_panel = ctk.CTkFrame(middle_frame, fg_color="transparent")
        right_panel.grid(row=0, column=1, sticky="nsew", padx=5, pady=5)
        right_panel.grid_columnconfigure(0, weight=1)
        right_panel.grid_rowconfigure(0, weight=1)
        right_panel.grid_rowconfigure(1, weight=2)

        self.episode_list_frame = ctk.CTkScrollableFrame(right_panel, label_text="Episodes")
        self.episode_list_frame.grid(row=0, column=0, sticky="nsew")

        self.description_textbox = ctk.CTkTextbox(right_panel, wrap="word", state="disabled")
        self.description_textbox.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        
        # --- Bottom Frame ---
        bottom_frame = ctk.CTkFrame(self)
        bottom_frame.grid(row=2, column=0, padx=10, pady=10, sticky="ew")
        ctk.CTkLabel(bottom_frame, text="Quality:").pack(side="left", padx=(10, 5), pady=10)
        self.quality_menu = ctk.CTkOptionMenu(bottom_frame, values=["best", "1080p", "720p", "480p", "360p", "worst"])
        self.quality_menu.pack(side="left", padx=5, pady=10)
        ctk.CTkLabel(bottom_frame, text="Mode:").pack(side="left", padx=(10, 5), pady=10)
        self.mode_var = ctk.StringVar(value="sub")
        ctk.CTkRadioButton(bottom_frame, text="Sub", variable=self.mode_var, value="sub").pack(side="left", padx=5, pady=10)
        ctk.CTkRadioButton(bottom_frame, text="Dub", variable=self.mode_var, value="dub").pack(side="left", padx=5, pady=10)
        ctk.CTkLabel(bottom_frame, text="Player:").pack(side="left", padx=(10, 5), pady=10)
        self.player_entry = ctk.CTkEntry(bottom_frame, width=80)
        self.player_entry.insert(0, "mpv")
        self.player_entry.pack(side="left", padx=5, pady=10)
        self.download_button = ctk.CTkButton(bottom_frame, text="Download", command=self.download_episode, state="disabled")
        self.download_button.pack(side="right", padx=(5, 10), pady=10)
        self.play_button = ctk.CTkButton(bottom_frame, text="Play", command=self.play_episode, state="disabled")
        self.play_button.pack(side="right", padx=5, pady=10)
        
        # --- Status Bar ---
        self.status_bar = ctk.CTkLabel(self, text="Ready. Enter an anime to search.", text_color="gray")
        self.status_bar.grid(row=3, column=0, padx=10, pady=5, sticky="w")

    def _clear_frames(self, anime=True, episodes=True, description=True):
        if anime:
            for widget in self.anime_results_frame.winfo_children():
                widget.destroy()
        if episodes:
            for widget in self.episode_list_frame.winfo_children():
                widget.destroy()
        if description:
            self.description_textbox.configure(state="normal")
            self.description_textbox.delete("1.0", "end")
            self.description_textbox.configure(state="disabled")

    def _update_status(self, message):
        self.status_bar.configure(text=message)
        self.update_idletasks()

    def search_anime(self, event=None):
        query = self.search_entry.get()
        if not query:
            self._update_status("Error: Search query cannot be empty.")
            return

        self.last_query = query
        self._update_status(f"Searching for '{query}'...")
        self.search_button.configure(state="disabled")
        self._clear_frames()
        self.play_button.configure(state="disabled")
        self.download_button.configure(state="disabled")
        self.thumbnail_cache.clear()

        threading.Thread(target=self._search_thread, args=(query,)).start()
    
    def _search_thread(self, query):
        try:
            mode = self.mode_var.get()
            self.anime_list = self.api.search(query, mode)
            
            if not self.anime_list:
                self.after(0, self._update_status, f"No results found for '{query}'.")
                self.after(0, self.search_button.configure, {"state": "normal"})
                return

            self.after(0, self._update_status, f"Found {len(self.anime_list)} results. Fetching details...")
            
            threads = [threading.Thread(target=self._fetch_details_for_item, args=(item,)) for item in self.anime_list]
            for t in threads: t.start()
            for t in threads: t.join()

            self.after(0, self._populate_anime_results)
            self.after(0, self._update_status, "Details loaded. Please select an anime.")
        except Exception as e:
            self.after(0, self._update_status, f"An error occurred: {e}")
        finally:
            self.after(0, self.search_button.configure, {"state": "normal"})

    def _fetch_details_for_item(self, anime_item):
        """Fetches thumbnail + synopsis from cache or Jikan API (rate limited)."""
        try:
            cache_base = self._get_cache_base(anime_item['title'])
            img_path = cache_base + ".jpg"
            meta_path = cache_base + ".json"

            # --- Use cache if available ---
            if os.path.exists(img_path) and os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                anime_item['synopsis'] = meta.get("synopsis", "No description available.")

                pil_image = Image.open(img_path)
                self.thumbnail_cache[anime_item['id']] = ctk.CTkImage(
                    light_image=pil_image, dark_image=pil_image, size=THUMBNAIL_SIZE
                )
                return

            # --- Jikan API (rate-limited) ---
            with jikan_lock:
                elapsed = time.time() - last_jikan_time[0]
                if elapsed < 0.5:  # 2 req/sec max
                    time.sleep(0.5 - elapsed)
                last_jikan_time[0] = time.time()

                title = anime_item['title']
                response = requests.get(f"https://api.jikan.moe/v4/anime?q={title}&limit=1", timeout=10)
                response.raise_for_status()
                data = response.json().get('data', [])

            if data and 'images' in data[0]:
                synopsis = data[0].get('synopsis', 'No description available.')
                anime_item['synopsis'] = synopsis
                image_url = data[0]['images']['jpg']['image_url']

                # Save synopsis
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump({"synopsis": synopsis}, f)

                # Download + cache image
                image_response = requests.get(image_url, timeout=10)
                image_response.raise_for_status()
                with open(img_path, "wb") as f:
                    f.write(image_response.content)

                pil_image = Image.open(io.BytesIO(image_response.content))
                self.thumbnail_cache[anime_item['id']] = ctk.CTkImage(
                    light_image=pil_image, dark_image=pil_image, size=THUMBNAIL_SIZE
                )
                return

        except Exception as e:
            print(f"Could not fetch details for {anime_item['title']}: {e}")

        # --- Fallback defaults ---
        anime_item['synopsis'] = 'No description available.'
        self.thumbnail_cache[anime_item['id']] = self.placeholder_image
      
    def _populate_anime_results(self):
        for i in range(3):
            self.anime_results_frame.grid_columnconfigure(i, weight=1, uniform="col")
    
        max_rows = (len(self.anime_list) + 2) // 3
        for r in range(max_rows):
            self.anime_results_frame.grid_rowconfigure(r, weight=1, uniform="row")

        for index, item in enumerate(self.anime_list):
            row, col = divmod(index, 3)
            thumbnail = self.thumbnail_cache.get(item['id'], self.placeholder_image)

            display_text = f"{item['title']}\n({item['episodes']} eps)"
            btn = ctk.CTkButton(
                self.anime_results_frame,
                text=display_text,
                image=thumbnail,
                compound="top",
                anchor="center"
            )
            btn.configure(command=lambda current_item=item: self.select_anime(current_item))
            btn.grid(row=row, column=col, padx=5, pady=5, sticky="nsew")

    def select_anime(self, item):
        self.selected_anime_id = item['id']
        self.selected_anime_index = item['index']
        self.selected_anime_title = item['title']
        
        self._clear_frames(anime=False, episodes=True, description=True)
        self.play_button.configure(state="disabled")
        self.download_button.configure(state="disabled")
        
        self.description_textbox.configure(state="normal")
        self.description_textbox.insert("1.0", item.get('synopsis', 'No description available.'))
        self.description_textbox.configure(state="disabled")

        self._update_status(f"Fetching episodes for '{item['title']}'...")
        threading.Thread(target=self._get_episodes_thread).start()

    def _get_episodes_thread(self):
        try:
            mode = self.mode_var.get()
            episodes = self.api.get_episodes(self.selected_anime_id, mode)
            self.after(0, self._populate_episodes, episodes)
            self.after(0, self._update_status, f"Select an episode for '{self.selected_anime_title}'.")
        except Exception as e:
            self.after(0, self._update_status, f"Could not fetch episodes: {e}")

    def _populate_episodes(self, episodes):
        for ep_num in episodes:
            btn = ctk.CTkButton(self.episode_list_frame, text=f"Episode {ep_num}", fg_color="transparent",
                                command=lambda e=ep_num: self.select_episode(e))
            btn.pack(fill="x", padx=5, pady=2)

    def select_episode(self, ep_num):
        self.selected_episode = ep_num
        self.play_button.configure(state="normal")
        self.download_button.configure(state="normal")
        self._update_status(f"Selected Episode {ep_num}. Ready to play or download.")

    def _run_ani_cli_command(self, action_flag=None):
        if not all([self.last_query, self.selected_anime_index, self.selected_episode]):
            self._update_status("Error: Anime and episode must be selected.")
            return

        command = [ANI_CLI_PATH]
        command.extend(["-q", self.quality_menu.get()])
        if self.mode_var.get() == "dub": command.append("--dub")
        if action_flag: command.append(action_flag)
        command.extend(["-S", str(self.selected_anime_index), "-e", str(self.selected_episode), self.last_query])
        
        action = "Downloading" if action_flag else "Playing"
        self._update_status(f"{action} Ep {self.selected_episode} of '{self.selected_anime_title}'...")

        try:
            env = os.environ.copy()
            if self.player_entry.get(): env["ANI_CLI_PLAYER"] = self.player_entry.get()
            
            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            subprocess.Popen(command, env=env, startupinfo=startupinfo)
        except Exception as e:
            self._update_status(f"Failed to execute command: {e}")

    def play_episode(self): self._run_ani_cli_command()
    def download_episode(self): self._run_ani_cli_command(action_flag="-d")


if __name__ == "__main__":
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    app = AniCliGUI()
    app.mainloop()
