# Ani-Cli_GUI
A python script to make ani-cli work as a gui (only tested on linux)

# To Run/Setup (Linux)
python -m venv Ani-Gui
source Ani-Gui/bin/activate
pip install customtkinter Pillow requests

# Info/Testing
Tested this on PopOs 22.04
Loading alot of shows takes a couple mins possibly ratelimit for getting thumbnails is set to 0.5seconds to not trigger too many requests
