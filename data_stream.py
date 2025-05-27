# Import necessary libraries
import requests
import pandas as pd
import json
from datetime import datetime, timezone
import time
import paho.mqtt.client as mqtt

# For Generate Unique Transaction Id for accesing API
import uuid

import config_secret

API_KEY = config_secret.API_KEY
AuthorizationHeader = config_secret.AuthorizationHeader
POLL_COOLDOWN = 60


class FuelPriceCheckAPI:
    def __init__(self, API_KEY, AuthorizationHeader):
        self.url_base = "https://api.onegov.nsw.gov.au" # Base Url
        self.API_KEY = API_KEY
        self.AuthorizationHeader = AuthorizationHeader

    
    def get_datetime_now():
        return datetime.now(timezone.utc).strftime("%d/%m/%Y %I:%M:%S %p")
    

    def get_unique_transactionId():
        return str(uuid.uuid4())

    def get_accesstoken(self):
        # Config url api prameters for getting accesstoken
        url_subpart = "/oauth/client_credential/accesstoken"

        url = self.url_base + url_subpart

        query_getAccessToken = {"grant_type":"client_credentials"}

        headers_getAccessToken = {'Authorization': self.AuthorizationHeader}

        # Get the web result for accesstoken
        response = requests.get(url, headers=headers_getAccessToken, params=query_getAccessToken)

        # Get the accesstoken from the web result
        access_token = response.json()["access_token"]

        return access_token


    def getFuelPrice(self):
        '''
        Returns all current fuel prices for all service stations. There may be restrictions on how often this API request can be made. It is recommended to execute this call in a separate api client as response can be over 2 mb. This API returns data for NSW.
        '''
        # Config url api prameters for getting fuel price
        url_subpart = "/FuelPriceCheck/v1/fuel/prices"

        url = self.url_base + url_subpart

        headers = {
            'Authorization': f'Bearer {self.get_accesstoken()}',
            'Content-Type': 'application/json; charset=utf-8',
            'apikey': self.API_KEY,
            'transactionid': FuelPriceCheckAPI.get_unique_transactionId(),
            'requesttimestamp': FuelPriceCheckAPI.get_datetime_now()
        }

        time.sleep(5)

        response = requests.get(url, headers=headers)

        return response



    def getNewFuelPrice(self):
        '''
        Returns all new current prices that have been submitted since the last "/fuelpricecheck/v1/fuel/prices" or "/fuelpricecheck/v1/fuel/prices/new" request using the apikey on the current day. This API returns data for NSW.
        '''
        # Config url api prameters for getting fuel price
        url_subpart = "/FuelPriceCheck/v1/fuel/prices/new"

        url = self.url_base + url_subpart

        headers = {
            'Authorization': f'Bearer {self.get_accesstoken()}',
            'Content-Type': 'application/json; charset=utf-8',
            'apikey': self.API_KEY,
            'transactionid': FuelPriceCheckAPI.get_unique_transactionId(),
            'requesttimestamp': FuelPriceCheckAPI.get_datetime_now()
        }

        time.sleep(5)

        response = requests.get(url, headers=headers)

        return response

def clean_and_display_fuel_data(df, column_width=70):
    # Drop rows with missing critical information
    df = df.dropna(subset=["ServiceStationName", "FuelCode", "PriceUpdatedDate", "Latitude", "Longitude", "Price"])

    # Fill missing values in non-critical columns
    df["Suburb"].fillna("Unknown", inplace=True)
    df["Postcode"].fillna("Unknown", inplace=True)

    # Convert FuelCode and Brand to categorical
    df["FuelCode"] = df["FuelCode"].astype("category")
    df["Brand"] = df["Brand"].astype("category")

    # Ensure Latitude, Longitude, and Price are numeric
    df["Latitude"] = pd.to_numeric(df["Latitude"], errors='coerce')
    df["Longitude"] = pd.to_numeric(df["Longitude"], errors='coerce')
    df["Price"] = pd.to_numeric(df["Price"], errors='coerce')

    # Remove invalid coordinates or prices
    df = df[
        (df["Price"] >= 0) &
        (df["Latitude"].between(-90, 90)) &
        (df["Longitude"].between(-180, 180))
    ]

    # Drop duplicate station-fuel-location combos
    df = df.drop_duplicates(subset=["ServiceStationName", "FuelCode", "Latitude", "Longitude"], keep="first")

    # Standardize text fields
    df["Brand"] = df["Brand"].str.title()
    df["Suburb"] = df["Suburb"].str.title()
    df["FuelCode"] = df["FuelCode"].str.upper()

    return df  

def fetch_and_save_fuel_data(fuelpriceAPI, output_file="integrated_fuel_data.csv", column_width=70):
    # Step 1: Fetch data from the API
    response = fuelpriceAPI.getFuelPrice()
    data = response.json()

    # Step 2: Create station mapping
    station_map = {
        station["code"]: {
            "ServiceStationName": station.get("name"),
            "Address": station.get("address"),
            "Brand": station.get("brand"),
            "Latitude": station.get("location", {}).get("latitude"),
            "Longitude": station.get("location", {}).get("longitude")
        }
        for station in data.get("stations", [])
    }

    # Step 3: Combine station info with prices
    combined_data = []
    for price_entry in data.get("prices", []):
        station_code = price_entry.get("stationcode")
        station_info = station_map.get(station_code)

        if station_info:
            full_address = station_info["Address"]
            try:
                parts = full_address.split(", ")
                suburb_postcode = parts[-1].rsplit(" ", 2)
                suburb = suburb_postcode[0]
                postcode = suburb_postcode[-1]
            except Exception:
                suburb, postcode = None, None

            combined_data.append({
                "ServiceStationName": station_info["ServiceStationName"],
                "Address": full_address,
                "Suburb": suburb,
                "Postcode": postcode,
                "Brand": station_info["Brand"],
                "FuelCode": price_entry.get("fueltype"),
                "Price": price_entry.get("price"),
                "PriceUpdatedDate": price_entry.get("lastupdated"),
                "Latitude": station_info["Latitude"],
                "Longitude": station_info["Longitude"]
            })

    # Step 4: Clean
    df = pd.DataFrame(combined_data)
    cleaned_df = clean_and_display_fuel_data(df)

    # Step 5: Save to CSV
    cleaned_df.to_csv(output_file, index=False)
    # print(f"Saved: {output_file}")


    return cleaned_df

def update_fuel_data(fuelpriceAPI, existing_file="integrated_fuel_data.csv", output_file="integrated_fuel_data.csv"):
    # Step 1: Load existing data
    # existing_df = pd.read_csv(existing_file)

    # Step 2: Fetch new data
    response = fuelpriceAPI.getNewFuelPrice()
    new_data_json = response.json()

    # Step 3: Extract station mapping
    station_map = {
        station["code"]: {
            "ServiceStationName": station.get("name"),
            "Latitude": station.get("location", {}).get("latitude"),
            "Longitude": station.get("location", {}).get("longitude")
        }
        for station in new_data_json.get("stations", [])
    }

    # Step 4: Extract new fuel prices (subset of full dataset)
    new_price_rows = []
    for price in new_data_json.get("prices", []):
        station_code = price.get("stationcode")
        station_info = station_map.get(station_code)
        if station_info:
            new_price_rows.append({
                "ServiceStationName": station_info["ServiceStationName"],
                "FuelCode": price.get("fueltype"),
                "Latitude": station_info["Latitude"],
                "Longitude": station_info["Longitude"],
                "Price": price.get("price"),
                "PriceUpdatedDate": price.get("lastupdated")
            })

    # Step 5: Clean
    df = pd.DataFrame(new_price_rows)
    cleaned_df = clean_and_display_fuel_data(df)

    # Step 6: Save updated file
    cleaned_df.to_csv(output_file, mode='a', index=False)

    return cleaned_df

def on_connect(client, userdata, connect_flags, reason_code, properties):
    print("Connected with result code", str(reason_code))
    # Subscribe as soon as we connect
    # client.subscribe("COMP5339/Assignment02/Group07/FuelPrice")

def on_message(client, userdata, msg):
    print("Message received")
    # print(json.loads(msg.payload))

def publish_data(df):
    # 1. Retrieve cleaned data
    # df = pd.read_csv("integrated_fuel_data.csv")
    
    # 2. Create client and attach callbacks
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    #client.on_connect = on_connect
    #client.on_message = on_message
    
    # 3. Connect to broker
    client.connect("172.17.34.107", 1883, keepalive=60)
    
    # 4. Start the network loop in a background thread
    client.loop_start()
    
    # 5. Publish periodically
    for row in df.itertuples():
    
        data = {
            "Index": row.Index, 
            "ServiceStationName": row.ServiceStationName,
            "Address": row.Address, 
            "Suburb": row.Suburb, 
            "Postcode": row.Postcode, 
            "Brand": row.Brand, 
            "FuelCode": row.FuelCode, 
            "Price": row.Price, 
            "PriceUpdatedDate": row.PriceUpdatedDate, 
            "Latitude": row.Latitude, 
            "Longitude": row.Longitude
        }
    
        client.publish("COMP5339/Assignment02/Group07/FuelPrice", json.dumps(data), qos=2)
    
        time.sleep(0.1)
    
    # 6. Clean up
    client.loop_stop()
    client.disconnect()

def main():
    print("Start Data Stream")
    # Construct API client class
    fuelpriceAPI = FuelPriceCheckAPI(API_KEY, AuthorizationHeader)

    # Retrieve and publish current price
    print("Retrieving Data from API")
    cleaned_df = fetch_and_save_fuel_data(fuelpriceAPI)
    print("Publishing Data to MQTT Broker")
    publish_data(cleaned_df)

    # Periodically check and publish price updates
    while(True):
        print(f"Sleeping for {POLL_COOLDOWN} seconds")
        time.sleep(POLL_COOLDOWN)
        print("Retrieving Data from API")
        updated_df = update_fuel_data(fuelpriceAPI)
        print("Publishing Data to MQTT Broker")
        publish_data(updated_df)


if __name__ == "__main__":
    main()