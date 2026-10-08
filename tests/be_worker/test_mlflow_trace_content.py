"""Synthetic text-only projections, without provider or tracking service calls."""
from copy import deepcopy
import json
import unittest
from apps.be_worker import mlflow_trace_content as content


class TraceContentTests(unittest.TestCase):
    def test_credentials_media_and_signed_urls_are_omitted_but_normal_text_survives(self):
        value = {'description':'차량은 정차했다. secret_actual https://x.invalid/v?X-Amz-Signature=secret_signed',
                 'api_key':'secret_structured', 'source':'https://docs.invalid/a',
                 'data':'secret_binary', 'note':'Authorization: Bearer secret_auth',
                 'bytes':b'secret_bytes', 'b64':'data:video/mp4;base64,'+'A'*300}
        result = content.content(value, secrets=('secret_actual',))
        text = json.dumps(result, ensure_ascii=False)
        for secret in ('secret_actual','secret_signed','secret_structured','secret_binary','secret_auth','secret_bytes'):
            self.assertNotIn(secret, text)
        self.assertIn('차량은 정차했다.', text)
        self.assertEqual(result['source'],'https://docs.invalid/a')
        self.assertNotIn('A'*300,text)

    def test_quoted_json_credentials_are_removed_from_visible_response_text(self):
        visible = json.dumps({'api_key':'dummy_unprefixed_credential',
            'Authorization':'Bearer dummy_auth_credential', 'description':'차량은 정차했다.'},
            ensure_ascii=False)
        raw = {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':visible}]}}]}
        for result in (content.clean_text(visible), content.gemini_response(raw)):
            saved = json.dumps(result, ensure_ascii=False)
            self.assertNotIn('dummy_unprefixed_credential', saved)
            self.assertNotIn('dummy_auth_credential', saved)
            self.assertIn('차량은 정차했다.', saved)
        self.assertNotIn('dummy_single_quoted',
            content.clean_text("{'api_key': 'dummy_single_quoted'}"))

    def test_actual_system_user_response_json_and_provenance_are_nonmutating(self):
        request = {'systemInstruction':{'parts':[{'text':'영상에 있는 사실만 관찰.'}]},
            'contents':[{'role':'user','parts':[{'text':'차량 접촉 장면을 분석.'},
                {'inlineData':{'mimeType':'video/mp4','data':'secret_binary'},'videoMetadata':{'fps':4}}]}],
            'generationConfig':{'temperature':1.0,'responseMimeType':'application/json','private':'secret_extra'}}
        original = deepcopy(request)
        result = content.gemini_request(json.dumps(request).encode(), {'prompt_version':'v2',
            'contract_sha256':{'schema':'f'*64}, 'source':{'key':'secret_path'}})
        self.assertEqual(request, original)
        self.assertEqual(result['messages'][0]['content'][0]['text'],'영상에 있는 사실만 관찰.')
        self.assertEqual(result['messages'][1]['content'][1]['video_metadata'],{'fps':4})
        self.assertEqual(result['prompt_provenance']['contract_sha256']['schema'],'f'*64)
        self.assertNotIn('secret_',json.dumps(result))
        raw={'candidates':[{'finishReason':'STOP','content':{'parts':[
            {'text':'private thought','thought':True}, {'text':'{"description":"차량 접촉."}'}]}}]}
        saved=deepcopy(raw)
        response=content.gemini_response(raw)
        self.assertEqual(raw,saved)
        self.assertEqual(response['candidates'][0]['response_json']['description'],'차량 접촉.')
        self.assertNotIn('private thought',json.dumps(response))

    def test_truncation_is_explicit_for_text_items_and_invalid_json(self):
        result = content.content({'message':'상황 설명. '*20,'items':list(range(7))},text_limit=20,item_limit=3)
        self.assertIn('[truncated:',result['message'])
        self.assertEqual(result['items'][-1],{'truncated':True,'omitted_items':4})
        self.assertEqual(content.gemini_request(b'not-json')['capture_status'],'request_not_json')


if __name__ == '__main__':
    unittest.main()
