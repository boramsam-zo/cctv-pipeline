export type Model = 'X3D-S' | 'R(2+1)D-18';
export type Case = {
  id: string; title: string; location: string; time: string; model: Model;
  state: '검토 대기' | '자료 확인 필요' | '후보 없음'; kind: string; description: string;
  observations: string[]; checks: string[]; accent: string;
};
// UI fixtures only. No persisted incident, inference, or agency decision is represented.
export const cases: Case[] = [
  {id:'TEST-001',title:'교차로 차량 충돌 의심',location:'테스트 교차로 A',time:'14:45:10',model:'X3D-S',state:'검토 대기',kind:'충돌',accent:'#4f9cbd',description:'교차로 안에서 두 차량의 이동 경로가 겹치는 장면을 가정한 UI 테스트 사례입니다.',observations:['두 차량의 이동 경로가 겹침을 가정','교차로 내부 정차를 가정'],checks:['실제 원본 영상 연결 후 충돌 여부 확인','영상 시각과 위치 정보 확인']},
  {id:'TEST-002',title:'차량 단독 정차 의심',location:'테스트 도로 B',time:'14:10:13',model:'R(2+1)D-18',state:'자료 확인 필요',kind:'차량 단독',accent:'#aa8662',description:'도로 가장자리의 정차 차량을 가정합니다. 고장과 사고를 구분하기 위한 화면 테스트입니다.',observations:['도로 가장자리 정차를 가정','정차 원인은 확인되지 않음'],checks:['정차 전후 영상 확인','고장·일시 정차·사고 구분']},
  {id:'TEST-003',title:'다중 차량 접근 장면',location:'테스트 교차로 C',time:'12:39:51',model:'X3D-S',state:'검토 대기',kind:'다중 차량',accent:'#6c89b0',description:'여러 차량이 접근하는 장면의 정보 배치와 긴 문장 표시를 확인하는 가상 사례입니다.',observations:['복수 차량 접근을 가정','실제 충돌과 피해는 미확인'],checks:['차량별 이동 경로 확인','충돌 이후 상황 확인']},
  {id:'TEST-004',title:'영상 근거 확인 필요',location:'테스트 도로 D',time:'09:34:24',model:'R(2+1)D-18',state:'자료 확인 필요',kind:'미판정',accent:'#768981',description:'영상이나 분석 자료가 연결되지 않았을 때의 표시를 확인합니다. 빈 결과는 정상 판정으로 처리하지 않습니다.',observations:[],checks:['원본 및 후보 영상 연결 여부 확인']},
  {id:'TEST-005',title:'차로 정체 상황',location:'테스트 도로 E',time:'08:46:56',model:'X3D-S',state:'검토 대기',kind:'차로 점유',accent:'#7c7caf',description:'정체된 차로와 이동 차량이 함께 있는 상황을 가정한 가상 사례입니다.',observations:['차로 내 정체를 가정'],checks:['정체 원인과 지속 시간 확인','영상 사각지대 확인']},
  {id:'TEST-006',title:'사고 후보 미검출 표시',location:'테스트 도로 F',time:'08:12:59',model:'R(2+1)D-18',state:'후보 없음',kind:'후보 없음',accent:'#769b9c',description:'사고 후보가 없는 결과의 UI 테스트입니다. 후보 없음은 정상 확정과 다릅니다.',observations:[],checks:['검출 누락 가능성 검토']},
];
