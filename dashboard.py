import folium
import streamlit as st
import paho.mqtt.client as mqtt
import json
import queue
import threading
import time

from streamlit_folium import st_folium

CENTER_START = [-33.8688, 151.2093]
DEFAULT_IMG = "https://raw.githubusercontent.com/gale2307/Comp5339/Dashboard/icon/independent.png"
FUEL_CODES = ['DL', 'E10', 'P95', 'P98', 'U91', 'PDL', 'EV', 'LPG', 'E85', 'B20']
REFRESH_SEC = 30

mq = queue.Queue() # Global queue for storing MQTT messages


class Station:
    def __init__(self, service_station_name, address, brand, latitude, longitude):
        self.service_station_name = service_station_name
        self.address = address
        self.brand = brand
        self.latitude = latitude
        self.longitude = longitude
        self.fuelprice = {}

class Fuelprice:
    def __init__(self, fuelcode, price, price_updated_date):
        self.fuelcode = fuelcode
        self.price = price
        self.price_updated_date = price_updated_date
        

##############################
# MQTT FUNCTIONS
##############################

# Define callbacks
def on_connect(client, userdata, connect_flags, reason_code, properties):
    print("Connected with result code", str(reason_code))
    if reason_code == 0:
        print("Connected to broker")
        try:
            client.subscribe("COMP5339/Assignment02/Group07/FuelPrice")
        except Exception as e:
            print(f"Error unsubscribing: {e}")
    else:
        print("Bad connection, rc =", reason_code)

def on_message(client, userdata, msg):
    data = json.loads(msg.payload)
    mq.put(data)

def start_mqtt():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect, client.on_message = on_connect, on_message
    client.connect("broker.hivemq.com", 1883, 60)  #choose a public server instead of private: 172.17.34.107
    client.loop_forever()

##############################
# FOLIUM MAP
##############################

threading.Thread(target=start_mqtt, daemon=True).start()

if 'stations' not in st.session_state:
    st.session_state['stations'] = {}

if 'markers' not in st.session_state:
    st.session_state['markers'] = []

if 'center' not in st.session_state:
    st.session_state['center'] = CENTER_START

if 'zoom' not in st.session_state:
    st.session_state['zoom'] = 8

if "fuelcode" not in st.session_state:
    st.session_state["fuelcode"] = "E10"

st.set_page_config(page_title="Fuelprice dashboard", layout="wide")
st.title("Real-time Fuelprice dashboard")
st.session_state["fuelcode"] = st.selectbox("Fuelcode: ", FUEL_CODES)

map_area = st.empty()

i = 0
while True:
    
    while not mq.empty():
        print(f"Processing queue: {mq.qsize()}")

        data = mq.get()
        if data["ServiceStationName"]+data["Address"] not in st.session_state['stations'].keys():
            st.session_state["stations"][data["ServiceStationName"]+data["Address"]] = Station(
                service_station_name=data["ServiceStationName"], 
                address=data["Address"], 
                brand=data["Brand"], 
                latitude=data["Latitude"], 
                longitude=data["Longitude"]
            )
            print(data["Latitude"])
            print(data["Longitude"])

        if data["FuelCode"] not in st.session_state["stations"][data["ServiceStationName"]+data["Address"]].fuelprice.keys():
            st.session_state["stations"][data["ServiceStationName"]+data["Address"]].fuelprice[data["FuelCode"]] = Fuelprice(
                fuelcode=data["FuelCode"],
                price=data["Price"],
                price_updated_date=data["PriceUpdatedDate"]
            )
        else:
            st.session_state["stations"][data["ServiceStationName"]+data["Address"]].fuelprice[data["FuelCode"]].price = data["Price"]
            st.session_state["stations"][data["ServiceStationName"]+data["Address"]].fuelprice[data["FuelCode"]].price_updated_date = data["PriceUpdatedDate"]

    fg = folium.FeatureGroup(name="Markers")

    for _, station in st.session_state["stations"].items():

        if st.session_state["fuelcode"] not in station.fuelprice.keys():
            print(f"skipping station: {station.service_station_name}")
            continue

        price_rows = "".join(
            f"<tr><td>{fuel_type}</td><td style='text-align:middle;'>{fuel_data.price}</td><td style='text-align:right;'>{fuel_data.price_updated_date}</td></tr>"
            for fuel_type, fuel_data in station.fuelprice.items()
        )

        popup = f"""
            <div style="font-size: 14px;">
                <b>{station.service_station_name}</b><br>
                {station.address}<br><br>
                <table style="width: 100%; border-collapse: collapse;">
                    <thead><tr><th>Fuel Type</th><th>Price</th><th>Last Update</th></tr></thead>
                    <tbody>
                        {price_rows}
                    </tbody>
                </table>
            </div>
        """

        brand = station.brand.lower().replace(" ", "")
        image_url = f"https://raw.githubusercontent.com/gale2307/Comp5339/Dashboard/icon/{brand}.png"
        icon_price = station.fuelprice[st.session_state["fuelcode"]].price

        print(f"brand: {brand}")

        marker = folium.Marker(
            location=[float(station.latitude), float(station.longitude)],
            icon=folium.DivIcon(html=f"""
                <div style="display: flex; flex-direction: column; align-items: center; gap: 0px;">
                    <img src="{image_url}" 
                        onerror="this.onerror=null; this.src='{DEFAULT_IMG}';"
                        style="width:30px;height:30px;">
                    <div style="
                        font-size: 12px; 
                        color: white; 
                        background-color: blue;
                        display: inline-block;
                    ">{icon_price}</div>
                </div>
            """),
            popup=folium.Popup(popup, max_width=300),
        )

        fg.add_child(marker)

    m = folium.Map(location=CENTER_START, zoom_start=10)
    map_area = st_folium(
        m,
        center=st.session_state["center"],
        zoom=st.session_state["zoom"],
        key="new_"+str(i),
        feature_group_to_add=fg,
        height=600,
        width=2400,
    )
    i += 1


    time.sleep(REFRESH_SEC)