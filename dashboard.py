import folium
import streamlit as st
import paho.mqtt.client as mqtt
import json
import queue
import threading
import time

from streamlit_folium import st_folium

#   Constants Definition  
CENTER_START = [-33.8688, 151.2093] # Default map center coordinates (Sydney)
DEFAULT_IMG = "https://raw.githubusercontent.com/gale2307/Comp5339/Dashboard/icon/independent.png" # Default station icon URL
FUEL_CODES = ['DL', 'E10', 'P95', 'P98', 'U91', 'PDL', 'EV', 'LPG', 'E85', 'B20'] # List of supported fuel types
REFRESH_SEC = 30 # Map refresh interval in seconds

#   Data Class Definitions  
class Station:
    def __init__(self, service_station_name, address, brand, latitude, longitude):
        self.service_station_name = service_station_name
        self.address = address
        self.brand = brand
        self.latitude = latitude
        self.longitude = longitude
        self.fuelprice = {} # Dictionary to store Fuelprice objects, keyed by fuel_code

class Fuelprice:
    def __init__(self, fuelcode, price, price_updated_date):
        self.fuelcode = fuelcode
        self.price = price
        self.price_updated_date = price_updated_date
        
#   MQTT Functionality Module  
def on_connect(client, userdata, connect_flags, reason_code, properties):
    # Callback for when the client receives a CONNACK response from the server.
    print(f"[{time.strftime('%H:%M:%S')}] MQTT: on_connect called. Result code: {reason_code}")
    if reason_code == 0: # Connection successful
        print(f"[{time.strftime('%H:%M:%S')}] MQTT: Connected to broker successfully.")
        try:
            topic = "COMP5339/Assignment02/Group07/FuelPrice"
            client.subscribe(topic) # Subscribe to the designated topic
            print(f"[{time.strftime('%H:%M:%S')}] MQTT: Subscribed to topic '{topic}'")
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] MQTT: Error subscribing: {e}")
    else:
        print(f"[{time.strftime('%H:%M:%S')}] MQTT: Bad connection, rc = {reason_code}")

def on_message(client, userdata, msg):
    # Callback for when a PUBLISH message is received from the server.
    current_time_str = time.strftime('%H:%M:%S')
    try:
        # NOTE: Retrieve the message queue instance from userdata, set during client initialization.
        message_q_from_userdata = userdata 
        if message_q_from_userdata is None:
            print(f"[{current_time_str}] MQTT: Error - userdata (message_queue) is None in on_message!")
            return

        if not isinstance(message_q_from_userdata, queue.Queue):
            print(f"[{current_time_str}] MQTT: Error - userdata is not a Queue instance in on_message! Type: {type(message_q_from_userdata)}")
            return

        payload_str = msg.payload.decode('utf-8') # Decode message payload
        data = json.loads(payload_str) # Parse JSON data
        message_q_from_userdata.put(data) # Put parsed data into the shared queue
        
        # Reduce print frequency for queue size updates
        q_size = message_q_from_userdata.qsize()
        if q_size % 50 == 0 or q_size < 5 or q_size > 500: 
             print(f"[{current_time_str}] MQTT: Message put into userdata queue. Queue size now: {q_size}")
             
    except json.JSONDecodeError:
        print(f"[{current_time_str}] MQTT: Error decoding JSON: {msg.payload}")
    except Exception as e:
        print(f"[{current_time_str}] MQTT: Error in on_message: {e}")


def start_mqtt_thread_target(message_q_for_thread): # NOTE: Target function for the MQTT thread, receives the queue instance.
    # NOTE: Create MQTT client and set the passed queue instance as userdata.
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, userdata=message_q_for_thread)
    
    client.on_connect = on_connect
    client.on_message = on_message # on_message will now get the queue from userdata
    
    def on_disconnect(client, userdata, rc): # userdata is also passed to on_disconnect
        print(f"[{time.strftime('%H:%M:%S')}] MQTT: Disconnected with result code {rc}.")
    client.on_disconnect = on_disconnect

    print(f"[{time.strftime('%H:%M:%S')}] MQTT: start_mqtt thread trying to connect...")
    try:
        client.connect("broker.hivemq.com", 1883, 60) # Connect to MQTT broker
        client.loop_forever() # Blocking loop to process network traffic and dispatch callbacks
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] MQTT: Connection/loop error in start_mqtt: {e}")


#   Streamlit Application Main Logic  

# Initialize the message queue in session_state if it doesn't exist.
# This ensures the queue object persists across Streamlit reruns.
if 'message_queue' not in st.session_state:
    print(f"[{time.strftime('%H:%M:%S')}] Main: Initializing message_queue in session_state.")
    st.session_state.message_queue = queue.Queue()

# Start the MQTT background thread only once per session.
# Pass the queue instance from session_state to the thread.
if 'mqtt_thread_started' not in st.session_state:
    print(f"[{time.strftime('%H:%M:%S')}] Main: Starting MQTT background thread...")
    # NOTE: Pass the queue instance from session_state as an argument to the thread's target function.
    threading.Thread(target=start_mqtt_thread_target, args=(st.session_state.message_queue,), daemon=True).start()
    st.session_state.mqtt_thread_started = True

# Initialize other session_state variables for stations, map center, zoom, and default fuelcode.
if 'stations' not in st.session_state: st.session_state['stations'] = {}
if 'center' not in st.session_state: st.session_state['center'] = CENTER_START
if 'zoom' not in st.session_state: st.session_state['zoom'] = 8
if "fuelcode" not in st.session_state: st.session_state["fuelcode"] = "E10" # Default fuel type

# Set page configuration (must be the first Streamlit command).
st.set_page_config(page_title="Fuelprice dashboard", layout="wide")
st.title("Real-time Fuelprice dashboard") # Set application title.

# Create a selectbox for fuel code selection.
# The selected value is stored in session_state and its change triggers a rerun.
current_fuel_code = st.session_state.get("fuelcode", "E10")
try: 
    default_selectbox_index = FUEL_CODES.index(current_fuel_code)
except ValueError: 
    default_selectbox_index = FUEL_CODES.index("E10") # Fallback to E10 if saved fuelcode is invalid
st.session_state["fuelcode"] = st.selectbox("Fuelcode: ", FUEL_CODES, index=default_selectbox_index)

#   Main thread's message processing loop  
main_thread_mq = st.session_state.message_queue # Get the queue instance from session_state.

current_time_queue_check = time.strftime('%H:%M:%S')
# NOTE: Log the current size of the message queue (referenced from session_state).
print(f"[{current_time_queue_check}] Main: Checking st.session_state.message_queue. Current size: {main_thread_mq.qsize()}")
messages_processed_count = 0
MAX_MESSAGES_PER_RUN = 200 # Limit the number of messages processed per rerun to prevent long blocking.

# Process messages from the queue until it's empty or the processing limit is reached.
while not main_thread_mq.empty() and messages_processed_count < MAX_MESSAGES_PER_RUN:
    if messages_processed_count == 0: # Log entry into the processing loop.
        print(f"[{time.strftime('%H:%M:%S')}] Main: Entered 'while not main_thread_mq.empty()' loop using st.session_state.message_queue.")
    messages_processed_count += 1
    data = main_thread_mq.get() # Retrieve a message from the queue.
    
    # Extract data fields safely using .get().
    station_name = data.get("ServiceStationName")
    address = data.get("Address")
    brand = data.get("Brand")
    latitude = data.get("Latitude")
    longitude = data.get("Longitude")
    fuel_code = data.get("FuelCode")
    price = data.get("Price")
    price_updated_date = data.get("PriceUpdatedDate")

    # Skip processing if essential station identification or location data is missing.
    if not (station_name and address and latitude is not None and longitude is not None): 
        continue
    
    station_key = station_name + address # Create a unique key for the station.

    # If station is new, create and store a Station object in session_state.
    if station_key not in st.session_state['stations']:
        st.session_state["stations"][station_key] = Station(
            service_station_name=station_name, address=address, brand=brand, 
            latitude=latitude, longitude=longitude
        )
    
    # If station exists and fuel data is valid, update or add the fuel price.
    if station_key in st.session_state['stations'] and fuel_code and price is not None:
        current_station_obj = st.session_state["stations"][station_key]
        if fuel_code not in current_station_obj.fuelprice: # New fuel type for this station
            current_station_obj.fuelprice[fuel_code] = Fuelprice(
                fuelcode=fuel_code, price=price, price_updated_date=price_updated_date
            )
        else: # Existing fuel type, update price and date
            current_station_obj.fuelprice[fuel_code].price = price
            current_station_obj.fuelprice[fuel_code].price_updated_date = price_updated_date

# Log the number of messages processed in this run.
if messages_processed_count > 0:
    print(f"[{time.strftime('%H:%M:%S')}] Main: Exited 'while not main_thread_mq.empty()' loop. Processed {messages_processed_count} messages from st.session_state.message_queue.")
elif main_thread_mq.qsize() > 0 : # Queue has items but none were processed (e.g., due to MAX_MESSAGES_PER_RUN).
    print(f"[{time.strftime('%H:%M:%S')}] Main: st.session_state.message_queue has {main_thread_mq.qsize()} items, but no messages processed.")
else: # Queue was empty.
    print(f"[{time.strftime('%H:%M:%S')}] Main: st.session_state.message_queue was empty.")

#   Map Drawing Logic  
fg = folium.FeatureGroup(name="Markers") # Create a FeatureGroup to hold map markers.
markers_added_to_map = 0

# Iterate through all stations stored in session_state.
for station_key_loop, station_obj_loop in st.session_state["stations"].items():
    selected_fuel = st.session_state["fuelcode"] # Get the currently selected fuel code.

    # Filter: Skip station if it doesn't have the selected fuel type or if the price is None.
    if selected_fuel not in station_obj_loop.fuelprice or station_obj_loop.fuelprice[selected_fuel].price is None: 
        continue
    # Filter: Skip station if latitude or longitude is missing.
    if station_obj_loop.latitude is None or station_obj_loop.longitude is None: 
        continue
    
    # Prepare HTML for the marker's popup table, showing all available fuel prices for the station.
    price_rows = "".join(
        f"<tr><td style='padding: 4px;'>{fuel_type}</td><td style='text-align:center; padding: 4px;'>{fuel_data.price}</td><td style='text-align:right; padding: 4px;'>{fuel_data.price_updated_date}</td></tr>" 
        for fuel_type, fuel_data in station_obj_loop.fuelprice.items() if fuel_data.price is not None # Only include rows with valid prices
    )
    popup_html = f"""<div style="font-size: 14px; min-width: 250px;"><b>{station_obj_loop.service_station_name}</b><br>{station_obj_loop.address}<br><br><table style="width: 100%; border-collapse: collapse;"><thead style="background-color: #f0f0f0;"><tr><th style="padding: 5px; border-bottom: 1px solid #ccc;">Fuel</th><th style="padding: 5px; border-bottom: 1px solid #ccc;">Price</th><th style="padding: 5px; border-bottom: 1px solid #ccc;">Updated</th></tr></thead><tbody>{price_rows if price_rows else "<tr><td colspan='3' style='text-align:center; padding: 5px;'>No price data</td></tr>"}</tbody></table></div>"""
    
    # Prepare brand image URL and the price to display on the icon.
    brand_str = str(station_obj_loop.brand).lower().replace(" ", "") if station_obj_loop.brand else "unknown"
    image_url = f"https://raw.githubusercontent.com/gale2307/Comp5339/Dashboard/icon/{brand_str}.png"
    icon_price_val = station_obj_loop.fuelprice[selected_fuel].price # Price for the selected fuel type.
    
    try:
        # Create a Folium marker with a custom HTML DivIcon.
        marker = folium.Marker(
            location=[float(station_obj_loop.latitude), float(station_obj_loop.longitude)], 
            icon=folium.DivIcon(html=f"""<div style="display: flex; flex-direction: column; align-items: center; gap: 1px; font-family: Arial, sans-serif;"><img src="{image_url}" onerror="this.onerror=null; this.src='{DEFAULT_IMG}';" style="width:30px;height:30px; border-radius:4px; box-shadow: 0 1px 3px rgba(0,0,0,0.2);"><div style="font-size: 12px; color: white; font-weight: bold; background-color: #007bff; padding: 2px 4px; border-radius: 3px; display: inline-block;">{icon_price_val}</div></div>"""), 
            popup=folium.Popup(popup_html, max_width=400)
        )
        fg.add_child(marker) # Add marker to the FeatureGroup.
        markers_added_to_map += 1
    except Exception as e: 
        print(f"[{time.strftime('%H:%M:%S')}] Main: Error creating marker for {station_obj_loop.service_station_name}: {e}")

# Display an info message if no markers were added but stations exist and queue is empty (data might be missing for selected fuel).
if markers_added_to_map == 0 and len(st.session_state.get('stations', {})) > 0 and st.session_state.message_queue.qsize() == 0 :
    st.info(f"No stations currently have price data for the selected fuel ({st.session_state['fuelcode']}). Waiting for updates or try another fuel type.")

# Create the Folium map object, centered and zoomed based on session_state.
current_map_center = st.session_state.get('center', CENTER_START)
current_map_zoom = st.session_state.get('zoom', 8)
m = folium.Map(location=current_map_center, zoom_start=current_map_zoom)
m.add_child(fg) # Add the FeatureGroup (with markers) to the map.

# Render the map using st_folium.
# A fixed key "fuel_map" ensures this component is updated rather than recreated.
map_render_data = st_folium(m, key="fuel_map", height=600, width=1200, returned_objects=["last_center", "last_zoom"])

# If the user interacts with the map (pans or zooms), update session_state to preserve their view.
if map_render_data and map_render_data.get("last_center") and map_render_data.get("last_zoom"):
    if (st.session_state['center'] != map_render_data["last_center"] or 
        st.session_state['zoom'] != map_render_data["last_zoom"]):
        st.session_state['center'] = map_render_data["last_center"]
        st.session_state['zoom'] = map_render_data["last_zoom"]

#   Periodic Refresh Mechanism  
main_thread_mq_at_end = st.session_state.message_queue # Get queue reference for final log.
current_time_before_rerun = time.strftime('%H:%M:%S')
print(f"[{current_time_before_rerun}] Main: Reached end of script. Preparing to sleep for {REFRESH_SEC}s then rerun. Remaining st.session_state.message_queue size: {main_thread_mq_at_end.qsize()}")
time.sleep(REFRESH_SEC) # Pause execution for REFRESH_SEC seconds.
try:
    st.rerun() # Trigger a rerun of the Streamlit script from the top.
except Exception as e:
    print(f"[{time.strftime('%H:%M:%S')}] Main: Exception during st.rerun() call: {e}")