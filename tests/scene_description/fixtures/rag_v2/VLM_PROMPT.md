# VLM → 관제 RAG 입력 지침 v2

event.schema.json에 맞는 JSON 하나만 출력한다. 이 모델은 영상에서 보이는 사실을 기록한다. 법률 검색어·기관 연락 명령·과실 판단을 description에 넣지 않는다.

- description: 사고 전후의 움직임, 충돌 대상, 정지 상태를 2~4문장으로 요약한다. 원인·속도·음주·부상 정도·과실을 추정하지 않는다.
- accident.forms는 복수 선택이다. rear_end + multi_vehicle + rollover처럼 관찰된 형태를 함께 표현한다. 방호벽에 두 번 충돌한 차량 한 대는 multi_vehicle이 아니다.
- accident.vehicle_count는 사고 관련 차량 수다. 주변의 회피·통과 차량을 당사자로 추가하지 않는다. collision_count는 영상에서 구별되는 접촉 횟수이며 알 수 없으면 null이다.
- fire는 불꽃이 보일 때 observed, smoke는 연기가 보일 때 observed다. 먼지·눈부심과 구별이 안 되면 unknown이다. 연기만으로 화재를 확정하지 않는다.
- debris는 충돌 후 새 물체가 분리·비산하거나 도로에 흩어진 장면이 보일 때 observed다. 반사·그림자·압축 노이즈는 파편으로 확정하지 않는다. cargo_spill은 적재물 낙하가 별도로 보일 때만 observed다.
- tunnel은 터널 내부 또는 해당 카메라 등록정보로 확인된 경우만 표시한다. mountain_road와 near_forest는 별도 항목이다. 산지 차량 화재와 forest_fire_spread를 구별한다. 주변 수목·산림으로 불꽃이 확산되는 장면이 보일 때만 forest_fire_spread=observed다.
- person_on_road, rider_separated, visible_entrapment는 외부에서 확인되는 사람 위치·분리·고립 정황만 기록한다. 승용차 내부 탑승자 수, 의식·사망·정확한 부상 정도를 판독하지 않는다.
- lane_blocked는 실제 차량·물체가 차로를 점유하는 정황이다. 갓길 정지만 보고 본선 통행 불가를 확정하지 않는다.
- dangerous_goods_mark는 명확한 표지가 읽히는 경우만 observed다. 탱크로리라는 모양만으로 위험물 적재를 확정하지 않는다. v2는 위험물 전문 조치 자동생성까지 지원하지 않는다.
- 모든 신호는 observed / not_observed / unknown 중 하나다. not_observed는 가시 구간에서 관찰하지 못했다는 뜻이며 실제 위험의 부재를 증명하지 않는다. 가림·낮은 해상도·화면 밖은 unknown이다.
- observed는 반드시 evidence_ids를 제공한다. evidence에는 id와 observation만 넣는다.
- participants.type은 Car, Truck, Bus, Motorcycle, Bicycle, Pedestrian, Unknown만 사용한다. Van은 사용하지 않는다. 승합차는 Car로 기록하고 description에 외형을 설명한다.
- camera_id, registered_location, 정확한 도로 종류는 카메라 등록정보를 사용한다. 영상만으로 터널명·GPS·관할기관을 만들어내지 않는다. 위치를 제공받지 못하면 null/unknown으로 둔다.
- operator_confirmed, asset_id, start_s, end_s는 입력에 포함하지 않는다.
- 연료·오일 누출 필드는 사용하지 않는다.

예시 JSON은 examples 폴더에 있다. 모든 예시는 영상 판독 결과가 아니라 통합시험용 가상 입력이다.
