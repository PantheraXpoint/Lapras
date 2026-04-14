#!/usr/bin/env python3
"""
Enhanced Dashboard Subscriber - Monitors both context manager state and dashboard agent sensor data.
Subscribes to:
- 'dashboard/context/state' - Context manager agent states
- Dashboard agent updates - Aggregated sensor data
"""

import json
import logging
import time
import sys
import os
import paho.mqtt.client as mqtt
from datetime import datetime

# Add the project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class EnhancedDashboardSubscriber:
    def __init__(self, mqtt_broker: str = "143.248.55.82", mqtt_port: int = 1883, st = None):
        """Initialize the enhanced dashboard subscriber."""
        self.mqtt_broker = mqtt_broker
        self.mqtt_port = mqtt_port
        
        # Topics to subscribe to
        self.context_topic = "dashboard/context/state"
        self.dashboard_agent_topic = "virtual/dashboard/to/context/updateContext"
        self.command_result_topic = "dashboard/control/result"
        self.threshold_result_topic = "dashboard/threshold/result"

        # Store data from both sources
        self.context_data = {}
        self.sensor_data = {}
        
        # 모든 센서 데이터를 저장할 통합 딕셔너리
        self.all_sensors = {}
        self.all_agents = {}

        # 명령 관련
        self.command_results = {}  # 명령 결과 저장
        self.last_context_update = 0
        self.last_sensor_update = 0
        
        # 마지막으로 받은 조명 명령 메시지 저장
        self.last_light_command = {}
        
        # Streamlit 인스턴스 저장 (app에서 설정됨)
        self.st = st
        self.last_rerun_time = 0
        self.payload = None
        
        # Sensor type emojis for display
        self.sensor_icons = {
            "motion": "🏃", 
            "temperature": "🌡️",
            "door": "🚪",
            "activity": "⚡"
        }
        
        # Set up MQTT client
        client_id = f"EnhancedDashboardSubscriber-{int(time.time()*1000)}"
        self.mqtt_client = mqtt.Client(client_id=client_id)
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_message = self._on_message
        self.mqtt_client.on_disconnect = self._on_disconnect
        
        logger.info(f"Initialized enhanced dashboard subscriber with client ID: {client_id}")
        
        # Auto-start MQTT client for immediate usage (for test scripts)
        try:
            self.mqtt_client.connect(self.mqtt_broker, self.mqtt_port)
            self.mqtt_client.loop_start()
            logger.info(f"Auto-started MQTT client connection")
        except Exception as e:
            logger.warning(f"Could not auto-start MQTT connection: {e}")
        
        # st 객체가 전달되었는지 확인
        if st is not None:
            # print(f"__init__에서 받은 st 객체 타입: {type(st)}")
            # print(f"st.rerun 메서드 존재: {hasattr(st, 'rerun')}")
            # 필요한 경우 메서드 사용 가능성 확인
            try:
                if hasattr(st, "rerun"):
                    # print("st.rerun 메서드가 존재합니다.")
                    pass
            except Exception as e:
                # print(f"st 메서드 확인 중 오류: {e}")
                pass
        else:
            # print("__init__에서 st 객체를 받지 못했습니다!")
            pass

    def _on_connect(self, client, userdata, flags, rc):
        """Callback when connected to MQTT broker."""
        if rc == 0:
            logger.info(f"Successfully connected to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}")
            
            # Subscribe to context manager dashboard updates
            result1 = self.mqtt_client.subscribe(self.context_topic, qos=1)
            logger.info(f"Subscribed to context manager topic: {self.context_topic} (result: {result1})")
            
            # Subscribe to dashboard agent updates
            result2 = self.mqtt_client.subscribe(self.dashboard_agent_topic, qos=1)
            logger.info(f"Subscribed to dashboard agent topic: {self.dashboard_agent_topic} (result: {result2})")
            
            # Subscribe to command result updates
            result3 = self.mqtt_client.subscribe(self.command_result_topic, qos=1)
            logger.info(f"Subscribed to command result topic: {self.command_result_topic} (result: {result3})")
            
            # Subscribe to threshold configuration results
            result4 = self.mqtt_client.subscribe(self.threshold_result_topic, qos=1)
            logger.info(f"Subscribed to threshold result topic: {self.threshold_result_topic} (result: {result4})")

        else:
            logger.error(f"Failed to connect to MQTT broker. Result code: {rc}")

    def _on_disconnect(self, client, userdata, rc):
        """Callback when disconnected from MQTT broker."""
        if rc != 0:
            logger.warning(f"Unexpected disconnection from MQTT broker. Result code: {rc}")
        else:
            logger.info("Disconnected from MQTT broker")

    def _on_message(self, client, userdata, msg):
        """Callback when message is received."""
        try:
            # Decode and parse the message
            message_str = msg.payload.decode('utf-8')
            message_data = json.loads(message_str)
            self.payload = message_data
            # print("\n====== 수신된 메시지 ======")
            # print(f"토픽: {msg.topic}")
            # print("메시지 내용:")
            # print(json.dumps(message_data, indent=2, ensure_ascii=False))
            # print("===========================\n")
            
            # self.st 타입 검증 및 출력
            # print("self.st 타입:", type(self.st))
            if self.st is not None:
                # print("self.st.session_state 존재:", hasattr(self.st, 'session_state'))
                # print("self.st.rerun 존재:", hasattr(self.st, 'rerun'))
                if hasattr(self.st, 'rerun'):
                    # print("self.st.rerun 타입:", type(self.st.rerun))
                    pass
            
            # Process based on topic
            if msg.topic == self.context_topic:
                # Handle dashboard state update from context manager
                if "payload" in message_data:
                    # 수신된 컨텍스트 상태 메시지 출력 (디버깅용)
                    # print("\n===== 수신된 컨텍스트 상태 메시지 =====")
                    # print(f"토픽: {msg.topic}")
                    
                    # 메시지가 너무 길 수 있으므로 헤더 부분만 출력
                    # msg_header = {
                    #     "event": message_data.get("event", {}),
                    #     "source": message_data.get("source", {}),
                    #     "target": message_data.get("target", {})
                    # }
                    # print("메시지 헤더:")
                    # print(json.dumps(msg_header, indent=2, ensure_ascii=False))
                    
                    # payload 내 agents 키가 있는지 확인하고, 있다면 첫 번째 에이전트만 출력
                    # agents = message_data.get("payload", {}).get("agents", {})
                    # if agents:
                    #     first_agent_id = next(iter(agents))
                    #     first_agent = agents[first_agent_id]
                    #     print(f"샘플 에이전트 ({first_agent_id}):")
                    #     print(json.dumps({first_agent_id: first_agent}, indent=2, ensure_ascii=False)[:500] + "...")
                    
                    # print("================================\n")
                    
                    payload = message_data["payload"]
                    self.context_data = payload
                    self.last_context_update = time.time()
                    self.all_agents = payload.get('agents', {})

                    # Extract door and chair sensors
                    self._extract_sensor_data(payload)

                    # Check and store light agent information
                    for agent_id, agent_data in self.all_agents.items():
                        if agent_data.get('agent_type') == 'hue_light':
                            # Save the current state of the light agent
                            hue_light_data = agent_data.get('state', {}).get('power', {})
                            if hue_light_data:
                                # If no command is saved, create a template
                                if not self.last_light_command:
                                    self.last_light_command = {
                                        "event": {
                                            "id": "template",
                                            "type": "dashboardCommand",
                                            "location": "office",
                                            "contextType": "control",
                                            "priority": "High"
                                        },
                                        "source": {
                                            "entityType": "dashboard",
                                            "entityId": "dashboard-streamlit"
                                        },
                                        "target": {
                                            "entityType": "light", 
                                            "entityId": agent_id
                                        },
                                        "payload": {
                                            "command": "template",
                                            "value": True,
                                            "hue_light": hue_light_data
                                        }
                                    }
                                    # print(f"조명 에이전트 {agent_id} 상태 템플릿 저장됨")
            
            # Process messages from dashboard agent topic
            elif msg.topic == self.dashboard_agent_topic:
                # 수신된 대시보드 에이전트 메시지 출력 (디버깅용)
                # print("\n===== 수신된 대시보드 에이전트 메시지 =====")
                # print(f"토픽: {msg.topic}")
                
                # 메시지 헤더 출력
                # msg_header = {
                #     "event": message_data.get("event", {}),
                #     "source": message_data.get("source", {}),
                #     "target": message_data.get("target", {})
                # }
                # print("메시지 헤더:")
                # print(json.dumps(msg_header, indent=2, ensure_ascii=False))
                
                # payload 구조 확인
                if "payload" in message_data:
                    payload = message_data["payload"]
                    # print("Payload 구조:")
                    # if isinstance(payload, dict):
                    #     print(f"Keys: {list(payload.keys())}")
                        
                    #     # sensors 필드가 있는지 확인
                    #     if "sensors" in payload:
                    #         sensors = payload["sensors"]
                    #         print(f"Sensors 수: {len(sensors)}")
                    #         if sensors:
                    #             # 첫 번째 센서만 샘플로 출력
                    #             first_sensor_id = next(iter(sensors))
                    #             first_sensor = sensors[first_sensor_id]
                    #             print(f"샘플 센서 ({first_sensor_id}):")
                    #             print(json.dumps({first_sensor_id: first_sensor}, indent=2, ensure_ascii=False))
                    # else:
                    #     print(f"Payload 타입: {type(payload)}")
                
                # print("=====================================\n")
                
                # Check payload structure for this specific topic
                if "payload" in message_data:
                    payload = message_data["payload"]
                    # print(f"Dashboard agent 페이로드 키: {list(payload.keys()) if isinstance(payload, dict) else 'not a dict'}")
                    
                    # Check for sensors field in payload
                    if isinstance(payload, dict) and "sensors" in payload:
                        # Extract sensors data
                        self._extract_sensor_data_from_dashboard_agent(payload)
                        # Update timestamp
                        self.last_sensor_update = time.time()
                    else:
                        # print("Dashboard agent 페이로드에 sensors 필드가 없습니다")
                        pass
                else:
                    # print(f"Dashboard agent 메시지에 payload 필드가 없습니다")
                    pass
            
            # Handle command result messages
            elif msg.topic == self.command_result_topic:
                # Handle command result messages
                if "event" in message_data and message_data["event"].get("type") == "dashboardCommandResult":
                    if "payload" in message_data:
                        # 수신된 명령 결과 메시지 출력 (디버깅용)
                        # print("\n===== 수신된 명령 결과 메시지 =====")
                        # print(f"토픽: {msg.topic}")
                        # print(f"메시지 내용:")
                        # print(json.dumps(message_data, indent=2, ensure_ascii=False))
                        # print("================================\n")
                        
                        self._handle_command_result(message_data["payload"])
            
            # Handle threshold configuration result messages
            elif msg.topic == self.threshold_result_topic:
                if "event" in message_data and message_data["event"].get("type") == "dashboardThresholdCommandResult":
                    if "payload" in message_data:
                        self._handle_threshold_result(message_data["payload"])

            # 메시지를 받은 후 Streamlit 재실행 - 만약 Streamlit 환경에서 실행 중이라면
            try:
                # self.st가 설정되어 있는 경우에만 rerun 수행
                if self.st is not None and hasattr(self.st, 'session_state'):
                    # Streamlit 세션 상태에 현재 시스템 상태 저장
                    self.st.session_state['current_system_state'] = self.context_data
                    self.st.session_state['last_event_type'] = msg.topic
                    self.st.session_state['last_event_timestamp'] = time.time()
                    
                    # 강제 rerun 수행 (1초 내에 중복 rerun 방지)
                    current_time = time.time()
                    if current_time - self.last_rerun_time > 1:  # 1초 간격으로 rerun 제한
                        self.last_rerun_time = current_time
                        
                        # 중요: MQTT 콜백은 다른 스레드에서 실행되므로, rerun()은 작동하지 않을 수 있음
                        # 대신 세션 상태 플래그를 설정하여 메인 스레드에서 감지하도록 함
                        self.st.session_state['mqtt_update_received'] = True
                        self.st.session_state['mqtt_last_update_time'] = current_time
                        
                        # 디버깅 정보
                        # print(f"MQTT 메시지 수신: {msg.topic} - 세션 플래그 설정됨")
                        # print(f"mqtt_update_received = {self.st.session_state.get('mqtt_update_received', False)}")
                        # print(f"mqtt_last_update_time = {self.st.session_state.get('mqtt_last_update_time', 0)}")
                        
                    # print(f"Streamlit 세션 플래그 설정됨 - 현재 시간: {current_time}, 마지막 업데이트: {self.last_rerun_time}")
                else:
                    # print("Streamlit 인스턴스가 설정되지 않았거나 session_state가 없습니다.")
                    # print(f"self.st 타입: {type(self.st)}")
                    # print(f"self.st에 session_state 속성 존재: {hasattr(self.st, 'session_state') if self.st is not None else False}")
                    pass
            except Exception as st_e:
                # Rerun 오류는 무시 (메인 스레드가 아닐 때 발생하는 오류)
                # print(f"Streamlit 재실행 중 오류 발생: {st_e}")
                import traceback
                # traceback.print_exc()
                pass
                
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            logger.error(f"Raw message: {msg.payload.decode('utf-8', errors='ignore')[:200]}...")
            import traceback
            traceback.print_exc()
    
    def _extract_sensor_data(self, payload):
        """Extract sensors from payload and store them by type."""
        # 현재 타임스탬프
        current_time = time.time()
        
        # Check if agents data exists
        if 'agents' in payload:
            # Iterate through each agent
            for agent_id, agent_data in self.all_agents.items():
                # Check the agent's sensor data
                sensors = agent_data.get('sensors', {})
                
                # Iterate through each sensor
                for sensor_id, sensor_data in sensors.items():
                    sensor_type = sensor_data.get('sensor_type')
                    
                    # 모든 센서를 통합 저장소에 저장
                    self.all_sensors[sensor_id] = {
                        'value': sensor_data.get('value'),
                        'metadata': sensor_data.get('metadata', {}),
                        'agent_id': agent_id,
                        'last_update': sensor_data.get('last_update'),
                        'sensor_type': sensor_type
                    }
                # print(f"self.all_sensors: {json.dumps(self.all_sensors, indent=2, ensure_ascii=False)}")
        return self.all_sensors
    
    def get_door_sensors(self):
        """Get the current door sensors."""
        return {sensor_id: sensor_data for sensor_id, sensor_data in self.all_sensors.items()
                if sensor_data.get('sensor_type') == 'door'}
    
    def get_chair_sensors(self):
        """Get the current chair sensors."""
        return {sensor_id: sensor_data for sensor_id, sensor_data in self.all_sensors.items()
                if sensor_data.get('sensor_type') == 'activity'}
        
    def get_motion_sensors(self):
        """Get the current motion sensors."""
        return {sensor_id: sensor_data for sensor_id, sensor_data in self.all_sensors.items()
                if sensor_data.get('sensor_type') == 'motion'}

    def get_temp_sensors(self):
        """Get the current temperature sensors."""
        return {sensor_id: sensor_data for sensor_id, sensor_data in self.all_sensors.items()
                if sensor_data.get('sensor_type') == 'temperature'}

    def _handle_context_manager_update(self, event):
        """Handle updates from the context manager (agent states)."""
        try:
            self.context_data = event.payload
            self.last_context_update = time.time()
            
            # Display context manager update with more details
            # self._display_context_update()
            
        except Exception as e:
            logger.error(f"Error handling context manager update: {e}")

    def _handle_dashboard_agent_update(self, event):
        """Handle updates from the dashboard agent (sensor data)."""
        try:
            payload = event.payload
            state = payload.get("state", {})
            
            self.sensor_data = {
                "dashboard_status": state.get("dashboard_status", "unknown"),
                "total_sensors": state.get("total_sensors", 0),
                "sensors_online": state.get("sensors_online", 0),
                "sensors_offline": state.get("sensors_offline", 0),
                "last_activity_time": state.get("last_activity_time", 0),
                "activity_summary": state.get("activity_summary", {}),
                "individual_sensors": {},
                "timestamp": event.event.timestamp,
                "raw_state": state  # Keep the raw state for debugging
            }
            
            # Extract individual sensor data
            for key, value in state.items():
                if '_' in key and not key.startswith('dashboard') and not key.startswith('total') and not key.startswith('sensors') and not key.startswith('last') and not key.startswith('activity'):
                    # Parse sensor data fields like "infrared_1_distance", "motion_01_status", etc.
                    parts = key.split('_')
                    if len(parts) >= 3:
                        sensor_type = parts[0]
                        sensor_num = parts[1]
                        field_name = '_'.join(parts[2:])
                        sensor_id = f"{sensor_type}_{sensor_num}"
                        
                        if sensor_id not in self.sensor_data["individual_sensors"]:
                            self.sensor_data["individual_sensors"][sensor_id] = {"type": sensor_type}
                        
                        self.sensor_data["individual_sensors"][sensor_id][field_name] = value
            
            self.last_sensor_update = time.time()
            
            # Display sensor update
            # self._display_sensor_update()
            
        except Exception as e:
            logger.error(f"Error handling dashboard agent update: {e}")

    def _display_context_update(self):
        """Display context manager update (agent states) with full details."""
        current_time = datetime.now().strftime("%H:%M:%S")
        
        print(f"\n[{current_time}] 📊 CONTEXT MANAGER UPDATE")
        print("─" * 70)
        
        # Display summary
        if 'summary' in self.context_data:
            summary = self.context_data['summary']
            print(f"Total Agents: {summary.get('total_agents', 0)}")
            print(f"Known Agents: {summary.get('known_agents', [])}")
            print(f"Last Update: {datetime.fromtimestamp(summary.get('last_update', 0)).strftime('%H:%M:%S')}")
        
        # Display detailed agent information
        if 'agents' in self.context_data and self.context_data['agents']:
            print("\nDETAILED AGENT STATES:")
            for agent_id, agent_info in self.context_data['agents'].items():
                status = "🟢 ONLINE" if agent_info.get('is_responsive', False) else "🔴 OFFLINE"
                print(f"\n  {status} {agent_id.upper()}")
                print(f"    Type: {agent_info.get('agent_type', 'unknown')}")
                print(f"    Last Seen: {datetime.fromtimestamp(agent_info.get('last_update', 0)).strftime('%H:%M:%S')}")
                
                # Display agent state in detail
                state = agent_info.get('state', {})
                if state:
                    print(f"    State ({len(state)} fields):")
                    
                    # Group state fields for better display
                    core_fields = ['power', 'dashboard_status', 'activity_detected']
                    summary_fields = ['total_sensors', 'sensors_online', 'sensors_offline']
                    status_fields = [k for k in state.keys() if k.endswith('_status') or k.endswith('_proximity')]
                    sensor_fields = [k for k in state.keys() if any(k.startswith(f"{t}_") for t in ['infrared', 'motion', 'temperature', 'door', 'activity']) and k not in status_fields]
                    other_fields = [k for k in state.keys() if k not in core_fields + summary_fields + status_fields + sensor_fields]
                    
                    # Display core fields
                    for field in core_fields:
                        if field in state:
                            print(f"      {field}: {state[field]}")
                    
                    # Display summary fields
                    for field in summary_fields:
                        if field in state:
                            print(f"      {field}: {state[field]}")
                    
                    # Display activity summary if it exists
                    if 'activity_summary' in state:
                        activity = state['activity_summary']
                        print(f"      activity_summary: IR:{activity.get('infrared_active',0)} Motion:{activity.get('motion_active',0)} Temp:{activity.get('temperature_alerts',0)} Door:{activity.get('doors_open',0)} Activity:{activity.get('activity_detected',0)}")
                    
                    # Display status fields (grouped)
                    if status_fields:
                        print(f"      Status Fields ({len(status_fields)}):")
                        for field in sorted(status_fields):
                            print(f"        {field}: {state[field]}")
                    
                    # Display sensor value fields (grouped and limited)
                    if sensor_fields:
                        displayed_sensors = 0
                        print(f"      Sensor Fields ({len(sensor_fields)} total, showing first 10):")
                        for field in sorted(sensor_fields):
                            if displayed_sensors < 10:
                                print(f"        {field}: {state[field]}")
                                displayed_sensors += 1
                            else:
                                break
                        if len(sensor_fields) > 10:
                            print(f"        ... and {len(sensor_fields) - 10} more sensor fields")
                    
                    # Display other fields
                    if other_fields:
                        print(f"      Other Fields ({len(other_fields)}):")
                        for field in sorted(other_fields)[:5]:  # Show max 5
                            print(f"        {field}: {state[field]}")
                        if len(other_fields) > 5:
                            print(f"        ... and {len(other_fields) - 5} more fields")
                
                # Display sensors
                sensors = agent_info.get('sensors', {})
                if sensors:
                    print(f"    Sensors ({len(sensors)}):")
                    for sensor_key, sensor_value in list(sensors.items())[:5]:  # Show first 5
                        print(f"      {sensor_key}: {sensor_value}")
                    if len(sensors) > 5:
                        print(f"      ... and {len(sensors) - 5} more sensors")
        else:
            print("No agents found in context data")
        
        print()

    def _display_sensor_update(self):
        """Display dashboard agent sensor update with enhanced details."""
        current_time = datetime.now().strftime("%H:%M:%S")
        
        print(f"\n[{current_time}] 📡 DASHBOARD AGENT SENSOR UPDATE")
        print("─" * 70)
        
        # Display summary
        status = self.sensor_data.get("dashboard_status", "unknown")
        total = self.sensor_data.get("total_sensors", 0)
        online = self.sensor_data.get("sensors_online", 0)
        offline = self.sensor_data.get("sensors_offline", 0)
        
        print(f"Dashboard Status: {status}")
        print(f"Sensors: {online}/{total} online, {offline} offline")
        
        # Display activity summary
        activity = self.sensor_data.get("activity_summary", {})
        if activity:
            print(f"Activity Summary:")
            print(f"  🔴 Infrared Active: {activity.get('infrared_active',0)}")
            print(f"  🏃 Motion Active: {activity.get('motion_active',0)}")
            print(f"  🌡️ Temperature Alerts: {activity.get('temperature_alerts',0)}")
            print(f"  🚪 Doors Open: {activity.get('doors_open',0)}")
            print(f"  ⚡ Activity Detected: {activity.get('activity_detected',0)}")
        
        # Display last activity
        last_activity = self.sensor_data.get("last_activity_time", 0)
        if last_activity > 0:
            time_ago = time.time() - last_activity
            print(f"Last Activity: {time_ago:.1f}s ago ({datetime.fromtimestamp(last_activity).strftime('%H:%M:%S')})")
        
        # Display individual sensors with detailed information
        sensors = self.sensor_data.get("individual_sensors", {})
        if sensors:
            print(f"\nINDIVIDUAL SENSOR READINGS ({len(sensors)} sensors):")
            
            # Group sensors by type
            by_type = {}
            for sensor_id, sensor_info in sensors.items():
                sensor_type = sensor_info.get("type", "unknown")
                if sensor_type not in by_type:
                    by_type[sensor_type] = []
                by_type[sensor_type].append((sensor_id, sensor_info))
            
            for sensor_type, sensor_list in sorted(by_type.items()):
                icon = self.sensor_icons.get(sensor_type, "📡")
                print(f"\n  {icon} {sensor_type.upper()} SENSORS ({len(sensor_list)}):")
                
                for sensor_id, sensor_info in sorted(sensor_list):
                    # Format based on sensor type
                    if sensor_type == "infrared":
                        distance = sensor_info.get("distance", "?")
                        proximity = sensor_info.get("proximity", "?")
                        unit = sensor_info.get("unit", "")
                        print(f"    {sensor_id}: {distance}{unit} (status: {proximity})")
                    elif sensor_type == "motion":
                        motion = sensor_info.get("motion", "?")
                        status = sensor_info.get("status", "?")
                        print(f"    {sensor_id}: motion={motion} (status: {status})")
                    elif sensor_type == "temperature":
                        temp = sensor_info.get("temperature", "?")
                        unit = sensor_info.get("unit", "")
                        status = sensor_info.get("status", "?")
                        print(f"    {sensor_id}: {temp}{unit} (status: {status})")
                    elif sensor_type == "door":
                        door_open = sensor_info.get("open", "?")
                        status = sensor_info.get("status", "?")
                        print(f"    {sensor_id}: open={door_open} (status: {status})")
                    elif sensor_type == "activity":
                        active = sensor_info.get("active", "?")
                        status = sensor_info.get("status", "?")
                        print(f"    {sensor_id}: active={active} (status: {status})")
                    else:
                        # Generic display for unknown sensor types
                        fields = [f"{k}={v}" for k, v in sensor_info.items() if k != "type"]
                        print(f"    {sensor_id}: {', '.join(fields[:3])}")
        else:
            print("\nNo individual sensor data received yet.")
            
            # Debug: show raw state data
            raw_state = self.sensor_data.get("raw_state", {})
            if raw_state:
                print(f"\nRAW STATE DEBUG (showing first 10 fields):")
                for i, (key, value) in enumerate(sorted(raw_state.items())):
                    if i >= 10:
                        print(f"  ... and {len(raw_state) - 10} more fields")
                        break
                    print(f"  {key}: {value}")
        
        print()

    def _display_combined_summary(self):
        """Display combined summary of both context and sensor data."""
        print("\n" + "="*80)
        print(f"COMBINED DASHBOARD SUMMARY - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*80)
        
        # Context manager summary
        context_age = time.time() - self.last_context_update if self.last_context_update > 0 else float('inf')
        context_status = "🟢 CURRENT" if context_age < 30 else "🔴 STALE"
        
        print(f"Context Manager: {context_status} (last update: {context_age:.1f}s ago)")
        if self.context_data.get('summary'):
            summary = self.context_data['summary']
            print(f"  Total Agents: {summary.get('total_agents', 0)}")
            print(f"  Known Agents: {summary.get('known_agents', [])}")
        
        # Sensor dashboard summary
        sensor_age = time.time() - self.last_sensor_update if self.last_sensor_update > 0 else float('inf')
        sensor_status = "🟢 CURRENT" if sensor_age < 30 else "🔴 STALE"
        
        print(f"\nSensor Dashboard: {sensor_status} (last update: {sensor_age:.1f}s ago)")
        if self.sensor_data:
            total = self.sensor_data.get("total_sensors", 0)
            online = self.sensor_data.get("sensors_online", 0)
            offline = self.sensor_data.get("sensors_offline", 0)
            print(f"  Sensors: {online}/{total} online, {offline} offline")
            
            activity = self.sensor_data.get("activity_summary", {})
            if activity:
                print(f"  Activity: IR={activity.get('infrared_active',0)}, Motion={activity.get('motion_active',0)}, " +
                      f"Temp alerts={activity.get('temperature_alerts',0)}, Doors open={activity.get('doors_open',0)}")
        
        print("="*80 + "\n")

    def start(self):
        """Start the enhanced dashboard subscriber."""
        try:
            print("ENHANCED SENSOR DASHBOARD")
            print("=" * 50)
            print("Monitoring both:")
            print("• Context Manager (agent states)")
            print("• Dashboard Agent (sensor data)")
            print("\nPress Ctrl+C to stop.\n")
            
            logger.info(f"Connecting to MQTT broker at {self.mqtt_broker}:{self.mqtt_port}...")
            self.mqtt_client.connect(self.mqtt_broker, self.mqtt_port)
            logger.info("Starting MQTT client loop...")
            
            # Start background MQTT loop
            self.mqtt_client.loop_start()
            
            # Main loop - display combined summary every 30 seconds
            last_summary = 0
            try:
                while True:
                    time.sleep(1)
                    current_time = time.time()
                    
                    # Show combined summary every 30 seconds
                    if current_time - last_summary > 30:
                        self._display_combined_summary()
                        last_summary = current_time
                        
            except KeyboardInterrupt:
                logger.info("Dashboard subscriber interrupted by user")
                
        except Exception as e:
            logger.error(f"Error starting dashboard subscriber: {e}")
        finally:
            self.stop()

    def stop(self):
        """Stop the enhanced dashboard subscriber."""
        logger.info("Stopping enhanced dashboard subscriber...")
        if hasattr(self, 'mqtt_client') and self.mqtt_client:
            try:
                self.mqtt_client.loop_stop()
                self.mqtt_client.disconnect()
                logger.info("MQTT client stopped and disconnected")
            except Exception as e:
                logger.warning(f"Error stopping MQTT client: {e}")
        logger.info("Enhanced dashboard subscriber stopped.")

    def _handle_command_result(self, payload):
        """Handle command result message."""
        command_id = payload.get('command_id')
        if not command_id:
            # print("Command result missing command_id")
            return
        
        # Store the command result
        self.command_results[command_id] = payload
        
        # Debug output
        success = payload.get('success', False)
        message = payload.get('message', 'No message')
        # print(f"명령 결과 수신: {command_id} - 성공: {success}, 메시지: {message}")
        
        # Update the status of the pending command if it exists
        if hasattr(self, 'pending_commands') and command_id in self.pending_commands:
            self.pending_commands[command_id]['status'] = 'completed'
            self.pending_commands[command_id]['result'] = payload
    
    def get_command_results(self):
        """Get the command results."""
        return self.command_results

    def _extract_sensor_data_from_dashboard_agent(self, payload):
        """Extract sensor data from dashboard agent message format."""
        try:
            # 현재 타임스탬프
            current_time = time.time()
            # print("keys", self.all_sensors.keys())
            # Check if there's a sensors object
            sensors_data = payload.get("sensors", {})
            if not sensors_data:
                return False
            
            # 센서 데이터 처리
            for sensor_id, sensor_data in sensors_data.items():
                sensor_type = sensor_data.get('sensor_type', 'unknown')
                
                # 모든 센서를 통합 저장소에 저장
                self.all_sensors[sensor_id] = sensor_data
            
            # 센서 데이터가 있으면 True 반환
            return len(sensors_data) > 0
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            return False

    def send_command(self, agent_id, action_name, parameters=None):
        """
        Send command to ContextRuleManager using proper Event structure.
        """
        if hasattr(self, 'mqtt_client'):
            import uuid
            import time
            command_id = f"cmd-{uuid.uuid4().hex[:8]}"
            
            # Create proper Event structure that ContextRuleManager expects
            command_event = {
                "event": {
                    "id": command_id,
                    "timestamp": list(time.gmtime()),  # FIXED: Use list format instead of string
                    "type": "dashboardCommand",
                    "location": None,
                    "contextType": None,
                    "priority": "High"
                },
                "source": {
                    "entityType": "dashboard",
                    "entityId": "dashboard-streamlit"
                },
                "target": {
                    "entityType": "virtualAgent",
                    "entityId": agent_id
                },
                "payload": {
                    "agent_id": agent_id,
                    "action_name": action_name,
                    "priority": "High"
                }
            }
            
            # 추가 파라미터가 있으면 적용
            if parameters:
                command_event["payload"]["parameters"] = parameters
            
            # 명령 메시지를 JSON 문자열로 변환하여 발행
            command_message = json.dumps(command_event)
            self.mqtt_client.publish("dashboard/control/command", command_message, qos=1)
            
            # 전송된 명령 메시지 출력 (디버깅용)
            # print("\n===== 전송된 명령 메시지 (수정된 형식) =====")
            # print(f"토픽: dashboard/control/command")
            # print(f"메시지 내용: {command_message}")
            # print("========================================\n")
            
            # 명령 결과 추적용으로 command_id 반환
            return command_id

    def set_streamlit(self, st_instance):
        """앱에서 Streamlit 인스턴스를 설정합니다."""
        self.st = st_instance
        # print(f"set_streamlit 메서드에서 전달받은 st 객체 타입: {type(st_instance)}")
        
        # st 객체 검증
        if hasattr(st_instance, 'rerun'):
            # print("st.rerun 메서드 존재 - 정상")
            pass
        else:
            # print("⚠️ 경고: st 객체에 rerun 메서드가 없습니다!")
            pass
            
        if hasattr(st_instance, 'session_state'):
            # print("st.session_state 존재 - 정상")
            pass
        else:
            # print("⚠️ 경고: st 객체에 session_state 속성이 없습니다!")
            pass
            
        return self

    def get_all_sensors(self):
        """모든 센서 데이터를 하나의 딕셔너리로 반환합니다."""
        # 저장된 모든 센서 데이터 반환
        return self.all_sensors.copy()

    def _handle_threshold_result(self, payload):
        """Handle threshold configuration result message."""
        command_id = payload.get('command_id')
        if not command_id:
            return

        # Store the threshold result
        if not hasattr(self, 'threshold_results'):
            self.threshold_results = {}
        self.threshold_results[command_id] = payload

        # Debug output
        success = payload.get('success', False)
        message = payload.get('message', 'No message')
        agent_id = payload.get('agent_id', 'unknown')
        threshold_type = payload.get('threshold_type', 'unknown')

    def send_threshold_command(self, agent_id: str, threshold_type: str, config: dict):
        """
        Send threshold configuration command to ContextRuleManager.

        Args:
            agent_id: Target agent ID (e.g., "hue_light", "aircon")
            threshold_type: Type of threshold ("light", "temperature")
            config: Configuration dictionary with threshold values
        """
        if hasattr(self, 'mqtt_client'):
            import uuid
            import time
            command_id = f"threshold-{uuid.uuid4().hex[:8]}"

            # Create proper Event structure for threshold configuration
            threshold_command_event = {
                "event": {
                    "id": command_id,
                    "timestamp": list(time.gmtime()),
                    "type": "dashboardThresholdCommand",
                    "location": None,
                    "contextType": None,
                    "priority": "High"
                },
                "source": {
                    "entityType": "dashboard",
                    "entityId": "dashboard-streamlit"
                },
                "target": {
                    "entityType": "virtualAgent",
                    "entityId": agent_id
                },
                "payload": {
                    "agent_id": agent_id,
                    "threshold_type": threshold_type,
                    "config": config
                }
            }

            # Send the threshold configuration message
            command_message = json.dumps(threshold_command_event)
            self.mqtt_client.publish("dashboard/threshold/command", command_message, qos=1)

            return command_id

    def get_threshold_results(self):
        """Get the threshold configuration results."""
        if not hasattr(self, 'threshold_results'):
            self.threshold_results = {}
        return self.threshold_results

    def get_all_agents(self):
        return self.all_agents.copy()

def main():
    """Main function to run the enhanced dashboard subscriber."""
    subscriber = EnhancedDashboardSubscriber()
    subscriber.start()

if __name__ == "__main__":
    main() 