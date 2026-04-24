import os
import sys
import time
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
from utils.new_dashboard_subscriber import EnhancedDashboardSubscriber

def get_config():
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
    config = {
        'mongo': {
            'uri': os.getenv('MONGO_URI'),
            'database': os.getenv('MONGO_DATABASE', 'event_db'),
            'username': os.getenv('MONGO_USER'),
            'password': os.getenv('MONGO_PASSWORD'),
            'ttl': int(os.getenv('TTL_SECONDS', 24 * 60 * 60)) # Default to 24 hours
        }
    }
    return config


def get_event_db(config = None):
    if config is None:
        config = get_config()
    try:
        client = MongoClient(
            config['mongo']['uri'],
            username=config['mongo']['username'],
            password=config['mongo']['password'],
            authSource=config['mongo']['database'],
            serverSelectionTimeoutMS=5000  # Timeout for server selection
        )
        # Test the connection
        client.admin.command('ping')
        db = client[config['mongo']['database']]
        return db
    except ConnectionFailure as e:
        raise ConnectionError(f"Failed to connect to MongoDB: {e}")
    except OperationFailure as e:
        raise RuntimeError(f"MongoDB operation failed: {e}")
    except Exception as e:
        raise RuntimeError(f"An unknown error occurred while connecting to MongoDB: {e}")


def query_event_db(db, collection_name, query_filter=None):
    if query_filter is None:
        query_filter = {}
    try:
        c = db[collection_name]
        results = list(c.find(query_filter))
        return results
    except Exception as e:
        print(f"Error querying the database: {e}")
        return None


if __name__ == "__main__":
    config = get_config()
    try:
        db = get_event_db(config)
        print("Successfully connected to the event database.")
    except Exception as e:
        print(f"Error: {e}")
        exit(1)
    # Create the client object before connecting to the broker
    client = EnhancedDashboardSubscriber()
    client.mqtt_client.subscribe("dashboard/control/result", qos=1)
    client.mqtt_client.loop_start()
    print("MQTT client started and subscribed to 'dashboard/control/result'.")
    last_timestamps = {}
    while True:
        # Keep the script running to maintain the MQTT connection and store Data in the database
        data = client.get_all_sensors()
        for sensor_name, sensor_data in data.items():
            # Check if the sensor data has been updated since the last timestamp in korean timezone
            last_update = sensor_data.get("last_update")
            if not last_update:
                continue
            last_update = datetime.fromtimestamp(last_update, tz=timezone.utc).astimezone(timezone(timedelta(hours=9)))
            if sensor_name in last_timestamps and last_update <= last_timestamps[sensor_name]:
                    continue
            last_timestamps[sensor_name] = last_update
            collection = db[sensor_name]
            metadata = sensor_data.get("metadata", {}).copy()
            for key in ["sensor_name", "sensor_id", "last_communication"]:
                metadata.pop(key, None)
            document = {
                "value": sensor_data.get("value"),
                "metadata": metadata,
                "timestamp": last_update # stored as float
            }
            # Ensure the collection exists and has the TTL index
            if 'timestamp' not in collection.index_information():
                collection.create_index("timestamp", expireAfterSeconds=config['mongo']['ttl'])

            try:
                collection.insert_one(document)
            except Exception as e:
                print(f"Error inserting data into {sensor_name} collection: {e}")
                continue
        time.sleep(1)

