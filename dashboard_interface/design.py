class MeetingRoomDesign:
    # Use the IDs as written in the diagram
    element_positions = {
        # TV, temperature, Door
        'TV Screen': (4, 0),
        'temperature_3': (7, 0),
        'door_01': (8, 9),  # Located at the center of the room entrance
        # Light, Infrared (left center vertical line)
        'light_1': (0, 4),
        'distance_1': (0, 6),
        'distance_2': (0, 5),
        'distance_3': (0, 3),
        'distance_4': (0, 2),
        # Chairs (A row: left, B row: right, rows 2~6, 7B has same x as AC)
        'activity_s2a': (2, 2),
        'activity_s3a': (2, 3),
        'activity_s4a': (2, 4),
        'activity_s5a': (2, 5),
        'activity_s6a': (2, 6),
        'activity_s1b': (6, 1),
        'activity_s2b': (6, 2),
        'activity_s3b': (6, 3),
        'activity_s4b': (6, 4),
        'activity_s5b': (6, 5),
        'activity_s6b': (6, 6),
        'activity_s7b': (4, 7),  # Same x coordinate as AC (5)
        # Motion
        'motion_MW1': (0, 9),
        'motion_01': (1, 2),
        'motion_02': (1, 4),
        'motion_03': (1, 6),
        'motion_04': (2, 8),
        'motion_05': (6, 8),
        'motion_06': (7, 6),
        'motion_07': (7, 4),
        'motion_08': (7, 2),
        'motion_MF1': (8, 0),
        # infrared (right vertical line)
        'infrared_1': (8, 6),
        'infrared_2': (8, 5),
        'infrared_3': (8, 3),
        'infrared_4': (8, 2),
        # AC
        'tilt_1': (4, 2),
        'tilt_2': (4, 5),
    }
    cell_w = 80
    cell_h = 80
    width = 9 * cell_w
    height = 10 * cell_h

    def __init__(self):
        self.activity_color = "#F5F7FA"         # Chair (almost white)
        self.activity_empty_color = "#B0BEC5"   # Empty chair (soft gray)
        self.motion_color = "#FFD166"           # Motion (amber)
        self.infrared_color = "#EF476F"         # Infrared (magenta-red)
        self.temp_color = "#118AB2"             # Temperature (blue)
        self.light_color = "#FFD600"            # Light (bright yellow)
        self.door_color = "#073B4C"             # Door (navy)
        self.tv_color = "#222831"               # TV (slate)
        self.ac_color = "#06D6A0"               # AC (teal)
        #self.background_color = "#222"       # Background (deep charcoal)
        self.text_color = "#000000"                # Default text (dark)
        #self.text_color_dark_bg = "#FFF"        # For dark backgrounds

    def generate_meeting_room_svg(self, sensors=None):
        if sensors is None:
            sensors = {}
        # 1. 팝업 JS 함수 추가
        popup_js = """
        <script type="text/javascript">
        function showSensorInfo(sensorId, sensorType, value, x, y) {
            const existingPopup = document.getElementById('sensor-popup');
            if (existingPopup) { existingPopup.remove(); }
            const popup = document.createElement('div');
            popup.id = 'sensor-popup';
            popup.style.position = 'absolute';
            popup.style.left = (x + 10) + 'px';
            popup.style.top = (y + 10) + 'px';
            popup.style.backgroundColor = 'white';
            popup.style.border = '1px solid #ccc';
            popup.style.borderRadius = '5px';
            popup.style.padding = '10px';
            popup.style.boxShadow = '0 2px 5px rgba(0,0,0,0.2)';
            popup.style.zIndex = '1000';
            popup.style.maxWidth = '250px';
            let valueDisplay = value;
            if (sensorType === 'activity' || sensorType === 'motion' || sensorType === 'door') {
               valueDisplay = value!=false ? '활성화됨' : '비활성화됨';
            } else if (sensorType === 'temperature') {
                valueDisplay = value + '°C';
            }
            popup.innerHTML = `
                <div style="font-weight:bold;margin-bottom:5px;">센서 정보</div>
                <div>ID: ${sensorId}</div>
                <div>타입: ${sensorType}</div>
                <div>값: ${valueDisplay}</div>
                <div style="text-align:right;margin-top:5px;">
                    <button onclick="document.getElementById('sensor-popup').remove();" 
                    style="border:none;background:#f0f0f0;padding:3px 8px;border-radius:3px;cursor:pointer;">
                    닫기</button>
                </div>
            `;
            document.body.appendChild(popup);
            window.parent.stBridges.send('sensor-id-bridge', sensorId);
        }
        </script>
        """
        svg = [popup_js, f'<svg width="{self.width}" height="{self.height}" style="border:1px solid #333;">',
               f'<rect x="0" y="0" width="{self.width}" height="{self.height}" fill="none" stroke="#000" stroke-width="4"/>']
        # 요소 그리기
        for eid, (gx, gy) in self.element_positions.items():
            x = gx * self.cell_w + self.cell_w // 2
            y = gy * self.cell_h + self.cell_h // 2

            # 기존 라벨/타입 추론 코드
            if eid.startswith('activity'):
                label = 'Chair'
                default_color = self.activity_color
                sensor_type = 'activity'
            elif eid.startswith('motion'):
                label = 'Mot'
                default_color = self.motion_color
                sensor_type = 'motion'
            elif eid.startswith('distance'):
                label = 'Dist'
                default_color = self.infrared_color
                sensor_type = 'infrared'
            elif eid.startswith('infrared'):
                label = 'Ir'
                default_color = self.infrared_color
                sensor_type = 'infrared'
            elif eid.startswith('temperature'):
                label = 'Tem'
                default_color = self.temp_color
                sensor_type = 'temperature'
            elif eid.startswith('light'):
                label = 'Light'
                default_color = self.light_color
                sensor_type = 'light'
            elif eid.startswith('door'):
                label = 'Door'
                default_color = self.door_color
                sensor_type = 'door'
            elif eid.startswith('TV'):
                label = 'TV'
                default_color = self.tv_color
                sensor_type = 'tv'
            elif eid.startswith('tilt'):
                label = 'AC'
                default_color = self.ac_color
                sensor_type = 'tilt'
            else:
                label = eid
                default_color = "#888"
                sensor_type = 'unknown'

            # 센서 값 추출
            value = None
            if sensors and eid in sensors:
                value = sensors[eid].get('value')
                sensor_type = sensors[eid].get('sensor_type', sensor_type)
            else: 
                value = None
                sensor_type = "준비 중"
            # 색상만 동적으로 결정
            color = default_color
            onclick = f"showSensorInfo('{eid}', '{sensor_type}', {str(value).lower() if value is not None else 'null'}, event.clientX, event.clientY)"
            if value is not None:
                if sensor_type == 'activity':
                    color = "#4CAF50" if value else "#BDBDBD"
                elif sensor_type == 'motion':
                    color = "#FF9800" if value else "#FFE0B2"
                elif sensor_type == 'infrared':
                    if value is not None and value <150:
                        color = "#FF0000"  # 진한 빨간색
                    else:
                        color = "#FFB1C1"
                elif sensor_type == 'temperature':
                    if value is not None and value >= 25:
                        color = "#F44336"
                    else:
                        color = "#2196F3"
                elif sensor_type == 'light':
                    color = "#FFD600" if value else "#FFF9C4"
                elif sensor_type == 'door':
                    door_width = 40
                    door_height = 5
                    if value:  # 열린 문
                        svg.append(
                            f'<line x1="{x}" y1="{y}" x2="{x}" y2="{y + door_width}" ' +

                            f'stroke="#3F51B5" stroke-width="{door_height}" ' +
                            f'onclick="{onclick}" ' +
                            f'style="cursor:pointer;" />'
                        )
                        value="열림"
                    else:  # 닫힌 문
                        svg.append(
                            f'<line x1="{x}" y1="{y}" x2="{x - door_width}" y2="{y}" ' +
                            f'stroke="#3F51B5" stroke-width="{door_height}" ' +
                            f'onclick="{onclick}" ' +
                            f'style="cursor:pointer;" />'
                        )
                        value="닫힘"
                    # 문 라벨
                    svg.append(f'<text x="{x - 5}" y="{y - 10}" fill="#333333" font-size="10" text-anchor="end">{label} ({value})</text>')
            else:
                print("나머지", eid, color, sensor_type, value)

            if eid.startswith('activity'):
                svg.append(f'<circle cx="{x}" cy="{y}" r="28" fill="{color}" stroke="#222" stroke-width="2" onclick="{onclick}" style="cursor:pointer;"></circle>')
                svg.append(f'<text x="{x}" y="{y+5}" font-size="15" text-anchor="middle" fill="#222">{label}</text>')
            elif eid.startswith('motion'):
                svg.append(f'<rect x="{x-18}" y="{y-18}" width="36" height="36" rx="8" fill="{color}" stroke="#222" stroke-width="2" onclick="{onclick}" style="cursor:pointer;"/>')
                svg.append(f'<text x="{x}" y="{y+5}" font-size="14" text-anchor="middle" fill="#222">{label}</text>')
            elif eid.startswith('distance'):
                svg.append( f'<ellipse cx="{x}" cy="{y}" rx="20" ry="14" fill="{color}" stroke="#222" stroke-width="2" onclick="{onclick}" style="cursor:pointer;"/>')
                svg.append(f'<text x="{x}" y="{y + 5}" font-size="14" text-anchor="middle" fill="#222">{label}</text>')
            elif eid.startswith('infrared'):
                svg.append(f'<ellipse cx="{x}" cy="{y}" rx="20" ry="14" fill="{color}" stroke="#222" stroke-width="2" onclick="{onclick}" style="cursor:pointer;"/>')
                svg.append(f'<text x="{x}" y="{y+5}" font-size="14" text-anchor="middle" fill="#222">{label}</text>')
            elif eid.startswith('temperature'):
                svg.append(f'<rect x="{x-22}" y="{y-22}" width="44" height="44" rx="10" fill="{color}" stroke="#222" stroke-width="2" onclick="{onclick}" style="cursor:pointer;"/>')
                svg.append(f'<text x="{x}" y="{y+7}" font-size="14" text-anchor="middle" fill="#222">{label}</text>')
            elif eid.startswith('light'):
                svg.append(f'<circle cx="{x}" cy="{y}" r="18" fill="{color}" stroke="#222" stroke-width="2" onclick="{onclick}" style="cursor:pointer;"/>')
                svg.append(f'<text x="{x}" y="{y+5}" font-size="14" text-anchor="middle" fill="#222">{label}</text>')
            elif eid.startswith('TV'):
                svg.append(f'<rect x="{x-50}" y="{y-22}" width="100" height="44" rx="8" fill="{color}" stroke="#222" stroke-width="2" onclick="{onclick}" style="cursor:pointer;"/>')
                svg.append(f'<text x="{x}" y="{y+8}" font-size="16" text-anchor="middle" fill="#fff">{label}</text>')
            elif eid.startswith('tilt'):
                svg.append(f'<rect x="{x-35}" y="{y-35}" width="70" height="70" rx="16" fill="{color}" stroke="#222" stroke-width="2" onclick="{onclick}" style="cursor:pointer;"/>')
                svg.append(f'<text x="{x}" y="{y+10}" font-size="18" text-anchor="middle" fill="#222">{label}</text>')
        svg.append('</svg>')
        return ''.join(svg)