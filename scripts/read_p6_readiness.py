"""EC2: read current service/code/DB readiness. No configuration or data writes."""
import json
import subprocess


def run(args, *, input=None):
    value=subprocess.run(args,input=input,text=True,capture_output=True,timeout=30)
    if value.returncode:
        raise RuntimeError('readiness_command_failed')
    return value.stdout.strip()


def main():
    compose=['docker','compose','-f','/opt/cctv-stack/compose.yaml','exec','-T','--user','postgres','db',
             'psql','-X','-q','-A','-t','-P','pager=off','-v','ON_ERROR_STOP=1','-d','cctv']
    tables=['cctv_jobs.queue','cctv_jobs.flows','cctv_scene.descriptions','cctv_scene.report_runs',
            'cctv_rag.chunks','cctv_jobs.analysis_tasks','cctv_jobs.analysis_selections']
    query="SELECT json_build_object("+','.join("'"+t+"',to_regclass('"+t+"') IS NOT NULL" for t in tables)+');'
    present=json.loads(run(compose,input='BEGIN READ ONLY;\n'+query+'\nCOMMIT;'))
    receipts={}
    for table in tables:
        if present[table]:
            query=f"SELECT json_build_object('count',count(*),'md5',md5(COALESCE(string_agg(to_jsonb(t)::text,E'\\n' ORDER BY to_jsonb(t)::text),''))) FROM {table} t;"
            receipts[table]=json.loads(run(compose,input='BEGIN READ ONLY;\n'+query+'\nCOMMIT;'))
    statuses={}
    if present['cctv_jobs.queue']:
        statuses['queue']=json.loads(run(compose,input="BEGIN READ ONLY; SELECT COALESCE(json_agg(t),'[]') FROM (SELECT state->>'status' AS status, count(*) AS jobs FROM cctv_jobs.queue GROUP BY 1 ORDER BY 1)t; COMMIT;"))
    for table in ('cctv_jobs.flows','cctv_jobs.analysis_tasks'):
        if present[table]:
            statuses[table]=json.loads(run(compose,input=f"BEGIN READ ONLY; SELECT COALESCE(json_agg(t),'[]') FROM (SELECT status,paused,finished_at IS NOT NULL AS finished,count(*) AS jobs FROM {table} GROUP BY 1,2,3 ORDER BY 1,2,3)t; COMMIT;"))
    identifiers=run(['docker','ps','-aq','--filter','label=com.docker.compose.project=cctv']).split()
    services=[]
    for identifier in identifiers:
        item=json.loads(run(['docker','inspect',identifier]))[0]
        service=item['Config'].get('Labels',{}).get('com.docker.compose.service')
        if service not in ('be-api','be-worker','cctv-ui','db'):continue
        services.append({'service':service,'name':item['Name'],'state':item['State']['Status'],
            'image':item['Config']['Image'],'image_id':item['Image'],
            'code_and_spool':{m['Destination']:m['Source'] for m in item.get('Mounts',[])
                              if m['Destination'].startswith('/code/') or m['Destination']=='/spool'},
            'gemini_secret_mounted':any(m['Destination']=='/run/secrets/be_gemini_api_key' for m in item.get('Mounts',[]))})
    print(json.dumps({'status':'p6_readiness_read','tables':present,'table_receipts':receipts,
        'task_states':statuses,'services':services,'model_calls':0,'s3_requests':0,
        'database_writes':0,'configuration_changes':0},indent=2))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        raise SystemExit('p6_readiness_read_failed; '+type(exc).__name__+'; no changes requested') from None
