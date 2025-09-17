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
from datetime import datetime, date

# --- Configuration ---
ANI_CLI_PATH = "ani-cli"
THUMBNAIL_SIZE = (160, 225)
ANI_CACHE_DIR = "Ani-Cache"
APP_DATA_FILE = "ani-gui-data.json"
os.makedirs(ANI_CACHE_DIR, exist_ok=True)
# --- End Configuration ---

# Lock for Jikan rate limiting
jikan_lock = threading.Lock()
last_jikan_time = [0]  # mutable list to store last request time


class DataManager:
    """Handles saving and loading of application data (history, library)."""
    def __init__(self, file_path):
        self.file_path = file_path
        self.data = self._load_defaults()

    def _load_defaults(self):
        return {"history": [], "library": {}}

    def load(self):
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    loaded_data = json.load(f)
                    defaults = self._load_defaults()
                    defaults.update(loaded_data)
                    self.data = defaults
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading data file: {e}. Starting with fresh data.")
            self.data = self._load_defaults()

    def save(self):
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=4)
        except IOError as e:
            print(f"Error saving data file: {e}")

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value

class AniAPI:
    """Handles direct interaction with the AllAnime GraphQL API."""
    def __init__(self):
        # FIXED: Updated API endpoint and headers to match the working version
        self.api_url = "https://api.allanime.day/api"
        self.api_refr = "https://allmanga.to"
        self.agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0"
        self.headers = {"User-Agent": self.agent, "Referer": self.api_refr}

        self.shows_gql = '''
        query($search: SearchInput, $limit: Int, $page: Int, $translationType: VaildTranslationTypeEnumType, $countryOrigin: VaildCountryOriginEnumType) {
          shows(search: $search, limit: $limit, page: $page, translationType: $translationType, countryOrigin: $countryOrigin) {
            edges { _id name availableEpisodes __typename }
          }
        }
        '''
        self.episodes_list_gql = '''
        query ($showId: String!) {
          show(_id: $showId) { _id availableEpisodesDetail }
        }
        '''

    def _make_request(self, query_template, variables):
        params = {"variables": json.dumps(variables), "query": query_template}
        response = requests.get(self.api_url, params=params, headers=self.headers, timeout=20)
        response.raise_for_status()
        return response.json()['data']

    def search(self, query, mode='sub'):
        variables = {
            "search": {"allowAdult": False, "allowUnknown": False, "query": query},
            "limit": 40, "page": 1, "translationType": mode, "countryOrigin": "ALL"
        }
        data = self._make_request(self.shows_gql, variables)['shows']['edges']
        return self._format_results(data, mode)

    def browse(self, mode='sub', sort_by="update", page=1):
        # Simplified browse function
        try:
            variables = {
                "search": {"allowAdult": False, "allowUnknown": False, "query": ""},
                "limit": 21, "page": page, "translationType": mode, "countryOrigin": "ALL"
            }
            data = self._make_request(self.shows_gql, variables)['shows']['edges']
            return self._format_results(data, mode)
        except:
            return []

    def _format_results(self, data, mode):
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
        data = self._make_request(self.episodes_list_gql, variables)
        episodes_data = data['show']['availableEpisodesDetail'].get(mode, [])
        return sorted(episodes_data, key=float)

class AniCliGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.api = AniAPI()
        self.data_manager = DataManager(APP_DATA_FILE)
        self.data_manager.load()

        self.title("Ani-CLI GUI")
        self.geometry("1400x850")

        self.current_page_name = "search"
        self.anime_list = []
        self.selected_anime_id = None
        self.selected_anime_index = None
        self.selected_anime_title = None
        self.selected_episode = None
        self.last_query = ""
        self.thumbnail_cache = {}
        self.placeholder_image = self._create_placeholder_image()
        self.current_browse_page = 1

        self._setup_ui()
        self._setup_bindings()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)
        self._hide_details_panel()

    def on_closing(self):
        self._update_status("Saving data...")
        self.data_manager.save()
        self.destroy()

    def _create_placeholder_image(self):
        image = Image.new('RGB', THUMBNAIL_SIZE, (50, 50, 50))
        return ctk.CTkImage(light_image=image, dark_image=image, size=THUMBNAIL_SIZE)

    def _get_cache_base(self, title):
        safe_name = hashlib.md5(title.encode("utf-8")).hexdigest()
        return os.path.join(ANI_CACHE_DIR, safe_name)

    def _setup_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # --- Sidebar ---
        sidebar_frame = ctk.CTkFrame(self, width=200, corner_radius=0)
        sidebar_frame.grid(row=0, column=0, rowspan=2, sticky="nsw")
        sidebar_frame.grid_rowconfigure(6, weight=1)
        ctk.CTkLabel(sidebar_frame, text="Ani-GUI", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=20)
        
        self.sidebar_buttons = {
            "search": ctk.CTkButton(sidebar_frame, text="Search Anime", command=lambda: self.show_page("search")),
            "browse": ctk.CTkButton(sidebar_frame, text="Browse Anime", command=lambda: self.show_page("browse")),
            "history": ctk.CTkButton(sidebar_frame, text="History", command=lambda: self.show_page("history")),
            "library": ctk.CTkButton(sidebar_frame, text="Library", command=lambda: self.show_page("library")),
        }
        for btn in self.sidebar_buttons.values(): btn.pack(pady=10, padx=20, fill="x")
        self.sidebar_buttons["settings"] = ctk.CTkButton(sidebar_frame, text="Settings", command=lambda: self.show_page("settings"))
        self.sidebar_buttons["settings"].pack(side="bottom", pady=20, padx=20, fill="x")

        # --- Main Content Area ---
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(0, weight=1)
        
        # --- Create Shared Details Panel ---
        self._create_details_panel()

        self.pages = {}
        self._create_search_page()
        self._create_browse_page()
        self._create_history_page()
        self._create_library_page()
        self._create_settings_page()

        self.status_bar = ctk.CTkLabel(self, text="Ready. Enter an anime to search.", text_color="gray")
        self.status_bar.grid(row=1, column=1, padx=10, pady=5, sticky="w")
        
        self.show_page("search")

    def show_page(self, page_name):
        self.current_page_name = page_name
        for name, page in self.pages.items():
            if name == page_name:
                page.grid(row=0, column=0, sticky="nsew")
                if hasattr(self, f"_activate_{name}_page"):
                    getattr(self, f"_activate_{name}_page")()
            else:
                page.grid_forget()
        self._hide_details_panel()

    def _create_interactive_page_layout(self, parent):
        parent.grid_columnconfigure(0, weight=3)
        parent.grid_columnconfigure(1, weight=1)
        parent.grid_rowconfigure(1, weight=1)

        results_container = ctk.CTkFrame(parent)
        results_container.grid(row=1, column=0, columnspan=2, padx=(5, 0), pady=5, sticky="nsew")
        results_container.grid_columnconfigure(0, weight=1)
        results_container.grid_rowconfigure(0, weight=1)

        results_frame = ctk.CTkScrollableFrame(results_container)
        results_frame.grid(row=0, column=0, sticky="nsew")

        return results_container, results_frame

    def _create_search_page(self):
        page = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        page.grid_columnconfigure(0, weight=3)
        page.grid_columnconfigure(1, weight=1)
        page.grid_rowconfigure(1, weight=1)

        self.search_results_container, self.anime_results_frame = self._create_interactive_page_layout(page)

        top_frame = ctk.CTkFrame(page)
        top_frame.grid(row=0, column=0, columnspan=2, padx=5, pady=5, sticky="ew")
        top_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(top_frame, text="Search Anime:").grid(row=0, column=0, padx=5, pady=5)
        self.search_entry = ctk.CTkEntry(top_frame, placeholder_text="e.g., Solo Leveling")
        self.search_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.search_entry.bind("<Return>", self.search_anime)

        self.search_button = ctk.CTkButton(top_frame, text="Search", command=self.search_anime)
        self.search_button.grid(row=0, column=2, padx=5, pady=5)

        self.pages["search"] = page

    def _create_browse_page(self):
        page = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        page.grid_columnconfigure(0, weight=3)
        page.grid_columnconfigure(1, weight=1)
        page.grid_rowconfigure(1, weight=1)

        self.browse_results_container, self.browse_results_frame = self._create_interactive_page_layout(page)

        top_frame = ctk.CTkFrame(page)
        top_frame.grid(row=0, column=0, columnspan=2, padx=5, pady=5, sticky="ew")

        ctk.CTkLabel(top_frame, text="Sort by:").grid(row=0, column=0, padx=(10, 5), pady=5)
        self.browse_sort_var = ctk.StringVar(value="update")

        ctk.CTkRadioButton(top_frame, text="Recently Updated", variable=self.browse_sort_var,
                           value="update", command=self.browse_anime).grid(row=0, column=1, padx=5, pady=5)
        ctk.CTkRadioButton(top_frame, text="Popular", variable=self.browse_sort_var,
                           value="popular", command=self.browse_anime).grid(row=0, column=2, padx=5, pady=5)
        ctk.CTkRadioButton(top_frame, text="Newest", variable=self.browse_sort_var,
                           value="create", command=self.browse_anime).grid(row=0, column=3, padx=5, pady=5)

        self.page_label = ctk.CTkLabel(top_frame, text="Page: 1")
        self.page_label.grid(row=0, column=4, padx=10, pady=5)

        self.prev_page_button = ctk.CTkButton(top_frame, text="< Prev",
                                              command=self.prev_browse_page, width=80, state="disabled")
        self.prev_page_button.grid(row=0, column=5, padx=5, pady=5)

        self.next_page_button = ctk.CTkButton(top_frame, text="Next >", command=self.next_browse_page, width=80)
        self.next_page_button.grid(row=0, column=6, padx=10, pady=5)

        self.pages["browse"] = page


    def _create_details_panel(self):
        self.right_panel = ctk.CTkFrame(self, fg_color="transparent")
        self.right_panel.grid_columnconfigure(0, weight=1)
        self.right_panel.grid_rowconfigure(0, weight=1)
        self.right_panel.grid_rowconfigure(1, weight=2)

        episode_container = ctk.CTkFrame(self.right_panel)
        episode_container.grid(row=0, column=0, sticky="nsew")
        episode_container.grid_columnconfigure(0, weight=1)
        episode_container.grid_rowconfigure(0, weight=1)

        self.episode_list_frame = ctk.CTkScrollableFrame(episode_container, label_text="Episodes")
        self.episode_list_frame.grid(row=0, column=0, sticky="nsew")

        close_btn = ctk.CTkButton(self.episode_list_frame, text="X", command=self._hide_details_panel,
                                  width=28, height=28, anchor="center")
        close_btn.place(relx=1.0, x=-5, y=0, anchor="ne")

        self.description_textbox = ctk.CTkTextbox(self.right_panel, wrap="word", state="disabled", height=200)
        self.description_textbox.grid(row=1, column=0, sticky="nsew", pady=(10, 0))

        bottom_frame = ctk.CTkFrame(self.right_panel)
        bottom_frame.grid(row=2, column=0, pady=(10, 0), sticky="ew")
        bottom_frame.grid_columnconfigure(6, weight=1)

        ctk.CTkLabel(bottom_frame, text="Quality:").grid(row=0, column=0, padx=(10, 5), pady=5)
        self.quality_menu = ctk.CTkOptionMenu(bottom_frame,
                                              values=["best", "1080p", "720p", "480p", "360p", "worst"], width=100)
        self.quality_menu.grid(row=0, column=1, padx=5, pady=5)

        ctk.CTkLabel(bottom_frame, text="Mode:").grid(row=0, column=2, padx=(10, 5), pady=5)
        self.mode_var = ctk.StringVar(value="sub")
        ctk.CTkRadioButton(bottom_frame, text="Sub", variable=self.mode_var, value="sub").grid(row=0, column=3, pady=5)
        ctk.CTkRadioButton(bottom_frame, text="Dub", variable=self.mode_var, value="dub").grid(row=0, column=4, padx=5, pady=5)

        ctk.CTkLabel(bottom_frame, text="Player:").grid(row=0, column=5, padx=(10, 5), pady=5)
        self.player_entry = ctk.CTkEntry(bottom_frame)
        self.player_entry.insert(0, "mpv")
        self.player_entry.grid(row=0, column=6, padx=5, pady=5, sticky="ew")

        self.play_button = ctk.CTkButton(bottom_frame, text="Play", command=self.play_episode, state="disabled")
        self.play_button.grid(row=1, column=0, columnspan=4, pady=5, padx=5, sticky="ew")

        self.download_button = ctk.CTkButton(bottom_frame, text="Download",
                                             command=self.download_episode, state="disabled")
        self.download_button.grid(row=1, column=4, columnspan=3, pady=5, padx=5, sticky="ew")

    def _show_details_panel(self):
        page = self.pages[self.current_page_name]
        self.right_panel.grid(row=1, column=1, sticky="nsew", padx=5, pady=5, in_=page)
        if self.current_page_name == "search":
            self.search_results_container.grid_configure(column=0, columnspan=1, padx=(5, 0))
        elif self.current_page_name == "browse":
            self.browse_results_container.grid_configure(column=0, columnspan=1, padx=(5, 0))

    def _hide_details_panel(self):
        self.right_panel.grid_forget()
        if self.current_page_name == "search":
            self.search_results_container.grid_configure(column=0, columnspan=2, padx=(5, 5))
        elif self.current_page_name == "browse":
            self.browse_results_container.grid_configure(column=0, columnspan=2, padx=(5, 5))

    def _create_history_page(self):
        page = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)
        self.history_frame = ctk.CTkScrollableFrame(page, label_text="Watch History")
        self.history_frame.grid(row=0, column=0, sticky="nsew")
        self.pages["history"] = page

    def _create_library_page(self):
        page = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        # Configure grid for this page
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(0, weight=1)

        tabview = ctk.CTkTabview(page)
        # Use .grid() instead of .pack()
        tabview.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

        tabview.add("My Library")
        tabview.add("Updates")

        # Widgets inside the tabview can still use pack, as they have a different parent
        self.library_results_frame = ctk.CTkScrollableFrame(tabview.tab("My Library"))
        self.library_results_frame.pack(expand=True, fill="both")

        self.updates_frame = ctk.CTkScrollableFrame(tabview.tab("Updates"), label_text="New Episodes Available")
        self.updates_frame.pack(expand=True, fill="both")

        self.check_updates_button = ctk.CTkButton(tabview.tab("Updates"), text="Check for Updates", command=self.check_for_updates)
        self.check_updates_button.pack(pady=10)

        self.pages["library"] = page

    def _create_settings_page(self):
        page = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        # Configure grid for this page
        page.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(page, text="Settings", font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, pady=10, padx=20, sticky="w")

        data_frame = ctk.CTkFrame(page)
        data_frame.grid(row=1, column=0, pady=10, padx=20, sticky="ew")

        ctk.CTkLabel(data_frame, text="Data Management").pack(anchor="w", padx=10, pady=5) # pack is ok inside this new frame
        ctk.CTkButton(data_frame, text="Save Data Now", command=self.data_manager.save).pack(side="left", padx=10, pady=10)
        ctk.CTkButton(data_frame, text="Load Data from File", command=self.data_manager.load).pack(side="left", padx=10, pady=10)

        self.pages["settings"] = page

    def _setup_bindings(self):
        self.bind("<Escape>", lambda e: self._hide_details_panel())
        self.bind_all("<MouseWheel>", self._on_mousewheel)
        self.bind_all("<Button-4>", self._on_mousewheel)
        self.bind_all("<Button-5>", self._on_mousewheel)
        self.bind_all("<Control-a>", self._select_all_text)
        self.bind_all("<Control-A>", self._select_all_text)

    def _on_mousewheel(self, event):
        x, y = self.winfo_pointerxy()
        widget = self.winfo_containing(x, y)
        while widget is not None and not isinstance(widget, ctk.CTkScrollableFrame):
            widget = widget.master
        if isinstance(widget, ctk.CTkScrollableFrame):
            if event.num == 4 or event.delta > 0: widget._parent_canvas.yview_scroll(-1, "units")
            elif event.num == 5 or event.delta < 0: widget._parent_canvas.yview_scroll(1, "units")

    def _select_all_text(self, event):
        widget = self.focus_get()
        if isinstance(widget, (ctk.CTkEntry, ctk.CTkTextbox)):
            widget.select_range(0, 'end')
            return "break"
        return None

    def _clear_frames(self, anime=True, episodes=True, description=True):
        target_frames = []
        if anime: target_frames.extend([self.anime_results_frame, self.browse_results_frame, self.library_results_frame, self.updates_frame])
        if episodes: target_frames.append(self.episode_list_frame)
        for frame in target_frames:
            for widget in frame.winfo_children(): widget.destroy()
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
        self._clear_frames(anime=True, episodes=True, description=True)
        self.play_button.configure(state="disabled")
        self.download_button.configure(state="disabled")
        self.thumbnail_cache.clear()
        self._hide_details_panel()
        threading.Thread(target=self._search_thread, args=(query,)).start()

    def browse_anime(self, page_num=1):
        self.current_browse_page = page_num
        self._update_status(f"Browsing anime... Page {self.current_browse_page}")
        self._clear_frames(anime=True, episodes=True, description=True)
        self.thumbnail_cache.clear()
        self.page_label.configure(text=f"Page: {self.current_browse_page}")
        self.prev_page_button.configure(state="normal" if self.current_browse_page > 1 else "disabled")
        self._hide_details_panel()
        sort_by = self.browse_sort_var.get()
        threading.Thread(target=self._browse_thread, args=(sort_by, self.current_browse_page)).start()

    def next_browse_page(self): self.browse_anime(page_num=self.current_browse_page + 1)
    def prev_browse_page(self):
        if self.current_browse_page > 1: self.browse_anime(page_num=self.current_browse_page - 1)
            
    def _search_thread(self, query):
        try:
            mode = self.mode_var.get()
            self.anime_list = self.api.search(query, mode)
            self._process_fetched_results(self.anime_list, self.anime_results_frame, f"No results found for '{query}'.")
        except Exception as e:
            self.after(0, self._update_status, f"An error occurred: {e}")
        finally:
            self.after(0, self.search_button.configure, {"state": "normal"})
            
    def _browse_thread(self, sort_by, page):
        try:
            mode = self.mode_var.get()
            self.anime_list = self.api.browse(mode, sort_by, page)
            self._process_fetched_results(self.anime_list, self.browse_results_frame, "No anime found.")
        except Exception as e:
            self.after(0, self._update_status, f"An error occurred: {e}")
    
    def _process_fetched_results(self, results, target_frame, not_found_msg):
        if not results:
            self.after(0, self._update_status, not_found_msg)
            return
        self.after(0, self._update_status, f"Found {len(results)} results. Fetching details...")
        threads = [threading.Thread(target=self._fetch_details_for_item, args=(item,)) for item in results]
        for t in threads: t.start()
        for t in threads: t.join()
        self.after(0, self._populate_anime_results, results, target_frame)
        self.after(0, self._update_status, "Details loaded. Please select an anime.")

    def _fetch_details_for_item(self, anime_item):
        try:
            cache_base = self._get_cache_base(anime_item['title'])
            img_path, meta_path = cache_base + ".jpg", cache_base + ".json"
            if os.path.exists(img_path) and os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f: meta = json.load(f)
                anime_item['synopsis'] = meta.get("synopsis", "No description.")
                pil_image = Image.open(img_path)
                self.thumbnail_cache[anime_item['id']] = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=THUMBNAIL_SIZE)
                return
            with jikan_lock:
                elapsed = time.time() - last_jikan_time[0]
                if elapsed < 0.5: time.sleep(0.5 - elapsed)
                last_jikan_time[0] = time.time()
                response = requests.get(f"https://api.jikan.moe/v4/anime?q={anime_item['title']}&limit=1", timeout=10)
                response.raise_for_status()
                data = response.json().get('data', [])
            if data and 'images' in data[0]:
                synopsis = data[0].get('synopsis', 'No description available.')
                anime_item['synopsis'] = synopsis
                image_url = data[0]['images']['jpg']['image_url']
                with open(meta_path, "w", encoding="utf-8") as f: json.dump({"synopsis": synopsis}, f)
                image_response = requests.get(image_url, timeout=10)
                image_response.raise_for_status()
                with open(img_path, "wb") as f: f.write(image_response.content)
                pil_image = Image.open(io.BytesIO(image_response.content))
                self.thumbnail_cache[anime_item['id']] = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=THUMBNAIL_SIZE)
                return
        except Exception as e:
            print(f"Could not fetch details for {anime_item['title']}: {e}")
        anime_item['synopsis'] = 'No description available.'
        self.thumbnail_cache[anime_item['id']] = self.placeholder_image
    
    def _populate_anime_results(self, results, target_frame):
        for widget in target_frame.winfo_children(): widget.destroy()
        cols = 3
        for i in range(cols): target_frame.grid_columnconfigure(i, weight=1, uniform="col")
        for r in range((len(results) + cols - 1) // cols): target_frame.grid_rowconfigure(r, weight=1)
        for index, item in enumerate(results):
            row, col = divmod(index, cols)
            thumbnail = self.thumbnail_cache.get(item['id'], self.placeholder_image)
            display_text = f"{item['title']}\n({item['episodes']} eps)"
            btn = ctk.CTkButton(target_frame, text=display_text, image=thumbnail, compound="top", anchor="center")
            btn.configure(command=lambda current_item=item: self.select_anime(current_item))
            btn.grid(row=row, column=col, padx=5, pady=5, sticky="nsew")
            btn.bind("<Button-3>", lambda event, current_item=item: self._show_context_menu(event, current_item))

    def select_anime(self, item):
        self._show_details_panel()
        self.selected_anime_id, self.selected_anime_index, self.selected_anime_title = item['id'], item['index'], item['title']
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
        close_btn = ctk.CTkButton(self.episode_list_frame, text="X", command=self._hide_details_panel, width=28, height=28, anchor="center")
        close_btn.place(relx=1.0, x=-5, y=0, anchor="ne")
        for ep_num in episodes:
            btn = ctk.CTkButton(self.episode_list_frame, text=f"Episode {ep_num}", fg_color="transparent", command=lambda e=ep_num: self.select_episode(e))
            btn.pack(fill="x", padx=5, pady=2)

    def select_episode(self, ep_num):
        self.selected_episode = ep_num
        self.play_button.configure(state="normal")
        self.download_button.configure(state="normal")
        self._update_status(f"Selected Episode {ep_num}. Ready to play or download.")

    def _run_ani_cli_command(self, action_flag=None):
        if not all([self.last_query, self.selected_anime_index, self.selected_episode]):
            self._update_status("Error: Search query, anime, and episode must be selected.")
            return
        command = [ANI_CLI_PATH, "-q", self.quality_menu.get()]
        if self.mode_var.get() == "dub": command.append("--dub")
        if action_flag: command.append(action_flag)
        command.extend(["-S", str(self.selected_anime_index), "-e", str(self.selected_episode), self.last_query])
        action = "Downloading" if action_flag else "Playing"
        self._update_status(f"{action} Ep {self.selected_episode} of '{self.selected_anime_title}'...")
        try:
            env = os.environ.copy()
            if self.player_entry.get(): env["ANI_CLI_PLAYER"] = self.player_entry.get()
            startupinfo = subprocess.STARTUPINFO() if sys.platform == "win32" else None
            if startupinfo: startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            subprocess.Popen(command, env=env, startupinfo=startupinfo)
            if not action_flag: self.add_to_history()
        except Exception as e:
            self._update_status(f"Failed to execute command: {e}")

    def play_episode(self): self._run_ani_cli_command()
    def download_episode(self): self._run_ani_cli_command(action_flag="-d")
    def _activate_history_page(self): self._populate_history_frame()

    def add_to_history(self):
        history = self.data_manager.get("history")
        new_entry = {"title": self.selected_anime_title, "episode": self.selected_episode, "timestamp": datetime.now().isoformat(), "query": self.last_query, "index": self.selected_anime_index}
        if not history or (history[-1]['title'] != new_entry['title'] or history[-1]['episode'] != new_entry['episode']):
            history.append(new_entry)
            self.data_manager.set("history", history)
            self._update_status(f"Added '{self.selected_anime_title} - Ep {self.selected_episode}' to history.")

    def _populate_history_frame(self):
        for widget in self.history_frame.winfo_children(): widget.destroy()
        history = sorted(self.data_manager.get("history"), key=lambda x: x['timestamp'], reverse=True)
        today, yesterday = date.today(), date.fromtimestamp(time.time() - 86400)
        last_date_str = None
        for item in history:
            dt_object = datetime.fromisoformat(item['timestamp'])
            item_date = dt_object.date()
            if item_date == today: date_str = "Today"
            elif item_date == yesterday: date_str = "Yesterday"
            else: date_str = item_date.strftime("%A, %B %d, %Y")
            if date_str != last_date_str:
                ctk.CTkLabel(self.history_frame, text=date_str, font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(10,5))
                last_date_str = date_str
            entry_text = f"{dt_object.strftime('%I:%M %p')} - {item['title']} - Episode {item['episode']}"
            btn = ctk.CTkButton(self.history_frame, text=entry_text, fg_color="transparent", anchor="w", command=lambda i=item: self._play_from_history(i))
            btn.pack(fill="x", padx=10, pady=2)

    def _play_from_history(self, item):
        self.last_query = item['query']
        self.selected_anime_index = item['index']
        self.selected_episode = item['episode']
        self.selected_anime_title = item['title']
        self.play_episode()

    def _activate_library_page(self):
        self._populate_library_frame()
        self._populate_updates_frame()

    def _show_context_menu(self, event, item):
        menu = ctk.CTkFrame(self, border_width=1)
        in_library = item['id'] in self.data_manager.get("library")
        if in_library: ctk.CTkButton(menu, text="Remove from Library", command=lambda: self.remove_from_library(item['id'], menu)).pack(fill="x")
        else: ctk.CTkButton(menu, text="Add to Library", command=lambda: self.add_to_library(item, menu)).pack(fill="x")
        menu.place(x=event.x_root - self.winfo_rootx(), y=event.y_root - self.winfo_rooty())
        self.bind("<Button-1>", lambda e: menu.destroy(), add="+")

    def add_to_library(self, item, menu):
        library = self.data_manager.get("library")
        library[item['id']] = item
        self.data_manager.set("library", library)
        self._update_status(f"Added '{item['title']}' to library.")
        self._populate_library_frame()
        menu.destroy()

    def remove_from_library(self, item_id, menu=None):
        library = self.data_manager.get("library")
        if item_id in library:
            title = library.pop(item_id)['title']
            self.data_manager.set("library", library)
            self._update_status(f"Removed '{title}' from library.")
            self._populate_library_frame()
        if menu: menu.destroy()

    def _populate_library_frame(self):
        self._populate_anime_results(list(self.data_manager.get("library").values()), self.library_results_frame)

    def check_for_updates(self):
        self.check_updates_button.configure(state="disabled", text="Checking...")
        self._update_status("Checking for new episodes in your library...")
        threading.Thread(target=self._check_for_updates_thread).start()
    
    def _check_for_updates_thread(self):
        library = self.data_manager.get("library")
        updated_items = []
        for item_id, item_data in library.items():
            try:
                mode = self.mode_var.get()
                search_results = self.api.search(item_data['title'], mode)
                latest_data = next((res for res in search_results if res['id'] == item_id), None)
                if latest_data:
                    if latest_data['episodes'] > item_data['episodes']:
                        print(f"Update found for {item_data['title']}: {item_data['episodes']} -> {latest_data['episodes']}")
                        item_data['episodes'] = latest_data['episodes']
                        updated_items.append(item_data)
                time.sleep(0.5)
            except Exception as e:
                print(f"Error checking updates for {item_data['title']}: {e}")
        self.data_manager.set("library", library)
        self.after(0, self._finalize_updates, updated_items)

    def _finalize_updates(self, updated_items):
        self._populate_updates_frame(updated_items)
        self._update_status(f"Found updates for {len(updated_items)} shows!" if updated_items else "No new episodes found.")
        self.check_updates_button.configure(state="normal", text="Check for Updates")

    def _populate_updates_frame(self, updated_items=None):
        for widget in self.updates_frame.winfo_children(): widget.destroy()
        if updated_items is None:
            ctk.CTkLabel(self.updates_frame, text="Click 'Check for Updates' to scan your library.").pack(pady=20)
            return
        if not updated_items:
             ctk.CTkLabel(self.updates_frame, text="No new episodes found.").pack(pady=20)
             return
        threads = [threading.Thread(target=self._fetch_details_for_item, args=(item,)) for item in updated_items]
        for t in threads: t.start()
        for t in threads: t.join()
        self._populate_anime_results(updated_items, self.updates_frame)

if __name__ == "__main__":
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    app = AniCliGUI()
    app.mainloop()
