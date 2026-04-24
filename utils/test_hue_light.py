import urllib.request
import json
import colorsys
import sys


# Replace with your bridge IP and API username
BRIDGE_IP = "143.248.55.137:10090"
USERNAME = "P2laHGvjzthn7Ip5-fAAIbVB9ulu9OlHWk8L7Yex"

# Base API URL
BASE_URL = f"http://{BRIDGE_IP}/api/{USERNAME}"

# Step 1: Get list of lights
def get_light_ids():
    with urllib.request.urlopen(f"{BASE_URL}/lights") as response:
        lights_data = json.load(response)
        return lights_data.keys()  # light IDs are strings

# Step 2: Turn off each light
def turn_off_light(light_id):
    url = f"{BASE_URL}/lights/{light_id}/state"
    data = json.dumps({"on": False}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="PUT")
    with urllib.request.urlopen(req) as response:
        result = response.read().decode("utf-8")
        print(f"Turned off light {light_id}: {result}")

def turn_off_light(light_id):
    url = f"{BASE_URL}/lights/{light_id}/state"
    data = json.dumps({"on": False}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="PUT")
    with urllib.request.urlopen(req) as response:
        result = response.read().decode("utf-8")
        print(f"Turned off light {light_id}: {result}")
        
def turn_on_light(light_id):
    url = f"{BASE_URL}/lights/{light_id}/state"
    data = json.dumps({"on": True}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="PUT")
    with urllib.request.urlopen(req) as response:
        result = response.read().decode("utf-8")
        print(f"Turned on light {light_id}: {result}")

# =======================================================================
# change color
# =======================================================================

# --- Helper Functions ---

def get_light_ids():
    with urllib.request.urlopen(f"{BASE_URL}/lights") as response:
        lights_data = json.load(response)
        return lights_data.keys()

def set_light_color(light_id, hue_val, sat_val=254, bri_val=254):
    url = f"{BASE_URL}/lights/{light_id}/state"
    data = json.dumps({
        "on": True,
        "hue": hue_val,
        "sat": sat_val,
        "bri": bri_val
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="PUT")
    with urllib.request.urlopen(req) as response:
        result = response.read().decode("utf-8")
        print(f"Set light {light_id} color: {result}")

def color_name_to_hue(color_name):
    """Convert a simple color name or hex to a hue value in [0, 65535]."""
    # Basic fallback color mapping
    basic_colors = {
        "red": (0, 100, 100),
        "green": (120, 100, 100),
        "blue": (240, 100, 100),
        "yellow": (60, 100, 100),
        "cyan": (180, 100, 100),
        "magenta": (300, 100, 100),
        "orange": (30, 100, 100),
        "pink": (330, 50, 100),
        "white": (0, 0, 100)
    }

    if color_name.lower() not in basic_colors:
        raise ValueError(f"Unknown color: {color_name}")

    h, s, v = basic_colors[color_name.lower()]
    # Convert degrees (0–360) to Hue API range (0–65535)
    hue_api = int((h / 360.0) * 65535)
    sat = int((s / 100.0) * 254)
    bri = int((v / 100.0) * 254)
    return hue_api, sat, bri


# Main logic
if __name__ == "__main__":
    light_ids = get_light_ids()
    for light_id in light_ids:
        turn_on_light(light_id)
        print(light_id)




# # Replace with your actual bridge info
# BRIDGE_IP = "143.248.56.213:10090"
# USERNAME = "P2laHGvjzthn7Ip5-fAAIbVB9ulu9OlHWk8L7Yex"
# BASE_URL = f"http://{BRIDGE_IP}/api/{USERNAME}"


# # --- Main ---

# if __name__ == "__main__":
#     if len(sys.argv) != 2:
#         print("Usage: python3 set_all_lights_color.py <color>")
#         print("Example: python3 set_all_lights_color.py red")
#         sys.exit(1)

#     input_color = sys.argv[1]
#     try:
#         hue, sat, bri = color_name_to_hue(input_color)
#     except ValueError as e:
#         print(e)
#         sys.exit(1)

#     light_ids = get_light_ids()
#     for light_id in light_ids:
#         set_light_color(light_id, hue, sat, bri)