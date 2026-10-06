import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { cases as baseCases, type Case } from './data';
import { filterCases, pageCases } from './selectors.mjs';
import './style.css';

const cases: Case[] = Array.from({ length: 16 }, (_, index) => ({
  ...baseCases[index % baseCases.length],
  id: `MOCK-${String(index + 1).padStart(3, '0')}`,
  location: `목업 카메라 ${String(index + 1).padStart(2, '0')} · 테스트 교차로`,
  time: `${String(14 - Math.floor(index / 4)).padStart(2, '0')}:${String(45 - index * 2).padStart(2, '0')}:10`,
}));
const sections = ['사건 개요', '영상 관찰', '후보 구간 및 시간', '확인 필요 사항', '기관별 검토 근거', '검토 결과 및 한계'];
const agencyFixtures = [
  { name: '경찰 · 목업 검토', text: '차량 진행 방향과 교차로 내 위치를 먼저 확인하는 검토 카드입니다.', basis: '목업 문서 R1 · 영상 검토 체크리스트' },
  { name: '소방 · 목업 검토', text: '인명 피해·화재 여부는 영상만으로 확정하지 않고 추가 확인이 필요합니다.', basis: '목업 문서 R2 · 추가 확인 항목' },
  { name: '도로 관리 · 목업 검토', text: '차로 점유와 잔해 여부를 확인하는 화면 배치 예시입니다.', basis: '목업 문서 R3 · 도로 상태 점검 항목' },
];

function Scene({ item, annotated = false, playing = false }: { item: Case; annotated?: boolean; playing?: boolean }) {
  const seed = Number(item.id.split('-')[1]) || 1;
  return <div className={`scene ${playing ? 'playing' : ''}`}>
    <svg viewBox="0 0 800 450" role="img" aria-label={`${item.title} · 실제 영상이 아닌 CCTV 장면 목업`}>
      <rect width="800" height="450" fill="#5f7267" />
      <path d="M0 45h220v65H0zM565 15h235v92H565zM0 320h200v130H0zM600 320h200v130H600z" fill="#89918b" />
      <path d="M24 59h150v33H24zM595 30h160v49H595zM25 354h130v56H25zM635 350h122v65H635z" fill="#aeb3ae" />
      <path d="M225 0h350v450H225zM0 115h800v205H0z" fill="#343c43" />
      <path d="M400 0v100m0 235v115M0 218h210m385 0h205" stroke="#dec96d" strokeWidth="3" />
      <path d="M302 0v100m0 235v215M494 0v100m0 235v215M0 168h205m390 0h210M0 266h205m390 0h210" stroke="#adb3b6" strokeWidth="2" strokeDasharray="20 18" />
      {[0, 1, 2, 3, 4, 5, 6].map(n => <g key={n} fill="#b8babc"><rect x={236 + n * 46} y="93" width="24" height="15" /><rect x={236 + n * 46} y="327" width="24" height="15" /></g>)}
      {[0, 1, 2, 3].map(n => <g key={n} fill="#b8babc"><rect x="207" y={125 + n * 47} width="14" height="24" /><rect x="580" y={125 + n * 47} width="14" height="24" /></g>)}
      <g className="car-one" transform={`translate(${315 + seed % 3 * 18}, ${182 + seed % 2 * 10}) rotate(-12)`}><rect width="65" height="30" rx="6" fill="#d7e0e5" /><rect x="16" y="4" width="27" height="22" rx="3" fill="#6a8291" /></g>
      <g className="car-two" transform="translate(475,240) rotate(90)"><rect width="70" height="31" rx="6" fill={item.accent} /><rect x="16" y="4" width="32" height="23" rx="3" fill="#263b4b" /></g>
      <g transform="translate(85,145)"><rect width="67" height="30" rx="5" fill="#d7c99d" /><rect x="16" y="4" width="27" height="22" rx="3" fill="#5d6b73" /></g>
      {annotated && <g fill="none" stroke="#69e293" strokeWidth="2"><rect x="301" y="164" width="112" height="68" /><rect x="421" y="235" width="68" height="100" /><path d="M235 206h66m155 129v70" strokeDasharray="7 4" /><text x="305" y="157" fill="#69e293" stroke="none" fontSize="12">vehicle · MOCK</text></g>}
      <rect x="12" y="12" width="236" height="28" rx="3" fill="#111b29" opacity=".8" />
      <text x="22" y="31" fill="#fff" fontSize="13">CAM {String(seed).padStart(2, '0')} · {item.time} · UI MOCK</text>
      <text x="785" y="432" textAnchor="end" fill="#fff" fontSize="12">합성 CCTV 장면 · 실제 영상 아님</text>
    </svg>
  </div>;
}

function Badge({ item }: { item: Case }) {
  return <span className={`badge ${item.state === '자료 확인 필요' ? 'warning' : 'success'}`}>{item.state === '검토 대기' ? '요청 처리 완료' : item.state}</span>;
}

function App() {
  const [selected, setSelected] = useState<Case | null>(null);
  const [grid, setGrid] = useState(3);
  const [page, setPage] = useState(1);
  const [model, setModel] = useState('전체');
  const [search, setSearch] = useState('');
  const [state, setState] = useState('전체');
  const [tab, setTab] = useState('원본 영상');
  const [playing, setPlaying] = useState(false);
  const [revision, setRevision] = useState('관련성 검수본');
  const [notification, setNotification] = useState<'idle' | 'pending' | 'unread' | 'read'>('idle');
  const [captured, setCaptured] = useState(() => new Date().toLocaleTimeString('ko-KR'));

  useEffect(() => {
    if (notification !== 'pending') return;
    const timer = setTimeout(() => setNotification('unread'), 2000);
    return () => clearTimeout(timer);
  }, [notification]);

  function open(item: Case) {
    setSelected(item);
    setTab('원본 영상');
    setPlaying(false);
    setRevision('관련성 검수본');
    window.scrollTo({ top: 0 });
  }
  const filtered = filterCases(cases, search, model, state) as Case[];
  const pagination = pageCases(filtered, page, grid * grid);
  const counts = ['검토 대기', '자료 확인 필요', '후보 없음'].map(value => cases.filter(item => item.state === value).length);
  const reportTexts = selected ? [
    `${selected.location}에서 ${selected.title} 상황을 가정한 목업입니다. ${selected.description}`,
    selected.observations.length ? selected.observations.join(' · ') : '차량 접근과 교차로 통과 장면을 표시하는 목업입니다. 실제 관찰 결과는 아닙니다.',
    selected.state === '후보 없음' ? '목업 후보 없음 · 정상 확정이 아닙니다.' : '목업 후보 4.2초 · 후보 클립 2.0–6.0초. 화면 배치용 가상 시간이며 실제 모델 출력이 아닙니다.',
    selected.checks.join(' · '),
    agencyFixtures.map(item => `${item.name}: ${item.text}`).join('\n'),
    '영상·피해·사고 여부를 확정하지 않습니다. 모든 문장과 기관 카드는 UI 목업이며 실제 지침이나 법률 근거가 아닙니다.',
  ] : [];

  return <main className="control-shell">
    <header className="control-header"><h1>CCTV 사고 관제</h1><span className="mock-label">목업 데이터 · React 테스트</span><button onClick={() => setCaptured(new Date().toLocaleTimeString('ko-KR'))}>↻ 조회 새로고침</button></header>
    <p className="scope">목업 스냅샷 · 서버/API 연결 없음 · 조회 기록: {captured} · 집계 범위: 가상 작업 16개</p>
    {notification === 'unread' || notification === 'read' ? <div className="notification" role="status"><strong>{notification === 'unread' ? '새 알림 1건' : '알림 읽음'} · {cases[0].id}</strong><span>저장 사건 알림의 화면 목업</span><button onClick={() => open(cases[0])}>동일 사건 열기</button><button disabled={notification === 'read'} onClick={() => setNotification('read')}>알림 확인</button></div> : null}
    {!selected ? <>
      <section className="demo-controls"><strong>발표용 알림 재현</strong><button disabled={notification === 'pending'} onClick={() => setNotification('pending')}>{notification === 'pending' ? '2초 뒤 알림 표시…' : '알림 재현'}</button><button onClick={() => setNotification('idle')}>시연 초기화</button><small>화면 안에서만 동작 · 실제 알림/기관 전송 없음</small></section>
      <section className="statistics"><h2>관제 기록 집계</h2><div className="metrics">{[['고유 작업', 16], ['요청 처리 완료', counts[0] + counts[2]], ['작업 확인 필요', counts[1]], ['대기·처리 중', 0]].map(([label, count]) => <div className="metric" key={label}><span>{label}</span><strong>{count}</strong></div>)}</div>
        <p className="caption">목업 보고 16개 · 영상 후보 {16 - counts[2]}개 · 확정 사고 수 아님 · 접수시각과 사고시각은 별개</p>
        <div className="preparation-grid">
          <div className="preparation-table">
        <div className="table-heading"><h3>영상별 설명·보고 준비 현황</h3><label>영상 집계 모델 <select aria-label="모델 필터" value={model} onChange={e => { setModel(e.target.value); setPage(1); }}>{['전체', 'X3D-S', 'R(2+1)D-18'].map(value => <option key={value}>{value}</option>)}</select></label></div>
        <div className="table-wrap"><table><thead><tr>{['원본 식별자 · 목업', '최근 작업', '작업 상태', '설명', '보고', '분석 작업 수'].map(label => <th key={label}>{label}</th>)}</tr></thead><tbody>{cases.filter(item => model === '전체' || item.model === model).slice(0, 5).map(item => <tr key={item.id}><td>TEST-SOURCE-{item.id.slice(-3)}</td><td>{item.id}</td><td>{item.state}</td><td>목업 준비</td><td>목업 준비</td><td>1</td></tr>)}</tbody></table></div>

          </div>
        <div className="panel type-chart"><h3>관찰된 사고 유형 분포 · 목업</h3>{['충돌', '차량 단독', '다중 차량', '차로 점유'].map(kind => <div className="type-bar" key={kind}><span>{kind}</span><i style={{ width: `${cases.filter(item => item.kind === kind).length * 32}px` }} /><b>{cases.filter(item => item.kind === kind).length}</b></div>)}</div>
        </div>
      </section>
      <div className="gallery-heading"><h2>영상 목록</h2><div className="segmented">{[3, 4].map(value => <button key={value} aria-pressed={grid === value} onClick={() => { setGrid(value); setPage(1); }}>{value}×{value}</button>)}</div></div>
      <p className="caption">주석 프레임 목업 · 실제 CCTV 이미지/독립 사고 수가 아닙니다.</p>
      <div className="filters"><input aria-label="사건 검색" placeholder="영상명 · 위치 · 목업 ID 검색" value={search} onChange={e => { setSearch(e.target.value); setPage(1); }} /><select aria-label="상태 필터" value={state} onChange={e => { setState(e.target.value); setPage(1); }}>{['전체', '검토 대기', '자료 확인 필요', '후보 없음'].map(value => <option key={value}>{value}</option>)}</select><span>목록 {filtered.length}작업</span><div className="pagination"><button aria-label="이전 페이지" disabled={pagination.current === 1} onClick={() => setPage(pagination.current - 1)}>‹</button><span>{pagination.current} / {pagination.count}</span><button aria-label="다음 페이지" disabled={pagination.current === pagination.count} onClick={() => setPage(pagination.current + 1)}>›</button></div></div>
      <section className="gallery" style={{ '--grid': grid } as React.CSSProperties}>{(pagination.items as Case[]).map(item => <article className="panel card" key={item.id}><Scene item={item} annotated /><h3>영상 후보 · {item.model} · {item.id}</h3><p>{item.title}</p><div><Badge item={item} /> <span className="badge review">사람 검토 전 · 목업</span></div><p className="caption">접수 2026-10-06 {item.time} KST · 가상 시각</p><button onClick={() => open(item)} aria-label={`${item.id} 사건 확인`}>→ 사건 확인</button></article>)}</section>
      {!filtered.length && <div className="panel empty">조건에 맞는 작업이 없습니다.<button onClick={() => { setSearch(''); setModel('전체'); setState('전체'); }}>필터 초기화</button></div>}
    </> : <>
      <button className="back" onClick={() => { setSelected(null); setPlaying(false); }}>← 영상 목록으로</button>
      <div className="detail-layout"><aside className="panel monitor"><h3>CCTV 영상 모니터링</h3><p className="caption">목업 16작업 · 현재 선택 {selected.id}</p><div className="monitor-list">{cases.map(item => <button className={`monitor-card ${selected.id === item.id ? 'active' : ''}`} key={item.id} onClick={() => open(item)} aria-label={`${item.id} 영상 선택`}><div className="monitor-head"><strong>{item.model} · {item.id}</strong></div><Scene item={item} annotated /><span>{item.title}</span><small>{item.state} · {item.time}</small></button>)}</div><p className="caption">정지 미리보기 · 합성 CCTV 목업</p></aside>
        <div className="detail-body"><div className="report-version"><span>보고 판</span><div className="segmented">{['관련성 검수본', '최초 생성본'].map(value => <button key={value} aria-pressed={revision === value} onClick={() => setRevision(value)}>{value}</button>)}</div><small>두 판 모두 화면 목업</small></div>
          <section className="panel case-strip"><div><h2>영상 후보 · {selected.model} · {selected.id}</h2><Badge item={selected} /><span className="badge review">사람 검토 전</span></div><p>카메라: MOCK-CAM-{selected.id.slice(-3)} · 위치: {selected.location} · 영상 사건 시각: 목업 {selected.time}</p><p>VLM 관찰 유형: {selected.kind} · 가상 관찰 데이터</p></section>
          <div className="evidence-layout"><div className="evidence-main"><section className="panel media"><h3>영상 근거</h3><div className="tabs" role="tablist" aria-label="영상 근거">{['원본 영상', '사고 후보 클립', '박스 주석 이미지'].map(value => <button role="tab" aria-selected={tab === value} key={value} onClick={() => { setTab(value); setPlaying(false); }}>{value}</button>)}</div><div role="tabpanel" aria-label={tab}><p className="caption">{tab === '박스 주석 이미지' ? '박스·궤적 정지 이미지 목업' : '합성 장면 재생 목업 · 실제 동영상 아님'}</p><Scene item={selected} annotated={tab !== '원본 영상'} playing={playing} />{tab !== '박스 주석 이미지' && <div className="mock-player"><button aria-label={playing ? '목업 일시정지' : '목업 재생'} onClick={() => setPlaying(value => !value)}>{playing ? '❚❚' : '▶'}</button><div className={`mock-progress ${playing ? 'running' : ''}`}><i /></div><span>00:00 / 00:08 · 목업</span><span>음성 없음</span></div>}</div><p className="caption">{selected.state === '후보 없음' ? '목업 후보 없음 · 정상 확정이 아닙니다.' : '후보 클립: 목업 2–6초 · 모델 후보: 목업 4.2초 · 충돌 정답 시각 아님'}</p></section>
            <section className="panel observations"><h3>VLM 장면 관찰 · 목업</h3><p>{selected.description}</p><div className="observation-tags"><span>{selected.kind}</span><span>차로 점유 · 가상</span><span>피해 미확인</span></div><ul>{(selected.observations.length ? selected.observations : ['교차로 접근 차량을 표시하는 가상 관찰', '실제 사고 판정은 미실행']).map(text => <li key={text}>{text}</li>)}</ul><p className="caption">원본과 대조할 가상 관찰 문장 · 실제 모델 호출 없음</p></section></div>
            <div className="evidence-side"><section className="panel checks"><h3>확인 필요 사항</h3>{selected.checks.map((text, index) => <p key={text}><strong>{index + 1}.</strong> {text}</p>)}<p>인명 피해와 사고 원인은 추가 확인이 필요합니다. · 목업</p></section><section className="panel agencies"><h3>기관별 검토 근거 · 목업</h3>{agencyFixtures.map(item => <article key={item.name}><h4>{item.name}</h4><p>{item.text}</p><small>{item.basis}</small></article>)}<p className="caption">실제 기관 지침·법령을 인용한 자료가 아닙니다.</p></section></div></div>
          <section className="panel report"><div className="report-title"><h2>사건 검토 보고 · {revision}</h2><span className="mock-label">6절 목업</span></div><p className="caption">{selected.id} · 실제 보고서를 연결하기 전 화면 테스트 문서</p>{sections.map((title, index) => <article key={title}><h3>{index + 1}. {title}</h3><p>{reportTexts[index]}</p></article>)}<details><summary>문서 근거 원문 · 목업</summary>{agencyFixtures.map(item => <div key={item.basis}><h4>{item.basis}</h4><p>{item.text}</p><p className="caption">공식 원문/실제 출처 없음 · 레이아웃 테스트용 문장</p></div>)}</details></section>
          <section className="panel observations"><h3>관찰 상세 · 추가 확인</h3><p>차량 위치, 진행 방향, 정차 상황의 문장 길이와 정보 배치를 검토하기 위한 목업입니다.</p><p>가시 범위 밖의 상황, 인명 피해, 사고 원인은 미확인으로 유지합니다.</p></section>
          <details className="panel records"><summary>처리 기록 · 저장 식별 정보</summary><p>작업 ID: {selected.id} · 가상 식별자</p><p>GPU 호출: 0 · VLM 호출: 0 · DB 저장: 없음 · 실제 처리 시간: 미측정</p></details>
        </div>
      </div>
    </>}
    <footer>기존 Streamlit 관제 UI를 재현한 React 목업 · 기존 파일과 운영 서버는 변경하지 않았습니다.</footer>
  </main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
