"""Stored A/B evidence + corruption tests; no model imports or media decoding."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from apps.be_worker.ab_evidence import prepare, selected_event, validate_index, validate_local
from apps.be_worker.analysis_profiles import profile

ROOT = Path(__file__).resolve().parents[2]
P4 = ROOT / 'artifacts/runpod/image-p4-20261001-01/image-validation-20261001-01'
P3 = ROOT / 'artifacts/runpod/ab-results-20261001-01/comparison-20261001-01'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2))


def reseal(source):
    """Synthetic fixture only: regenerate hashes after a deliberate mutation."""
    raw = source/'raw'
    manifest = read(raw/'manifest.json')
    manifest['files'] = [{'path':p.relative_to(raw).as_posix(), 'bytes':p.stat().st_size,
                          'sha256':digest(p)} for p in sorted(raw.rglob('*'))
                         if p.is_file() and p != raw/'manifest.json']
    save(raw/'manifest.json', manifest)
    normalized = read(source/'result.json')
    normalized['artifacts'] = [{'path':p.relative_to(source).as_posix(), 'bytes':p.stat().st_size,
                               'sha256':digest(p)} for p in sorted(raw.rglob('*')) if p.is_file()]
    save(source/'result.json', normalized)


class ABEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def source(self, version='A', copy=False):
        path = P4/f'input_001-{version}'/'output'
        if copy:
            target = self.root/'source'
            shutil.copytree(path,target)
            return target
        return path

    def prepare(self, version='A', source=None, target=None, **kwargs):
        source = source or self.source(version)
        return prepare(source, target or self.root/'output', snapshot=kwargs.get('snapshot',profile(version)),
                       input_sha256=kwargs.get('input_sha256',read(source/'result.json')['input_sha256']),
                       result_sha256=kwargs.get('result_sha256',digest(source/'result.json')))

    def test_stored_a_exact_bytes_and_independent_copy(self):
        source = self.source()
        before = {p.relative_to(source).as_posix():digest(p) for p in source.rglob('*') if p.is_file()}
        output = self.prepare()
        self.assertEqual(output['status'],'ab_evidence_prepared')
        event = selected_event(output['index'],'event_000')
        self.assertEqual(event['frame_mime'],'image/png')
        self.assertEqual(event['frame_role'],'unannotated_frame')
        self.assertEqual(event['yolo_objects'],read(source/'raw/vlm_handoffs.json')[0]['yolo_objects'])
        for name, record in output['manifest']['files'].items():
            if name.startswith('raw/'):
                self.assertEqual(record['sha256'],before[name])
        self.assertEqual(before,{p.relative_to(source).as_posix():digest(p) for p in source.rglob('*') if p.is_file()})
        event['analysis_profile']['version']='B'
        self.assertEqual(output['index']['analysis_profile']['version'],'A')

    def test_stored_b_preserves_all_media_context_and_rounding(self):
        result=self.prepare('B')
        source=self.source('B')
        event=selected_event(result['index'],'event_000')
        self.assertNotIn('yolo_objects',event)
        self.assertEqual(event['frame_mime'],'image/jpeg')
        self.assertEqual(event['frame_role'],'annotated_frame')
        self.assertEqual(event['representative_source_time_s'],1.44)
        self.assertNotEqual(event['display_sample_time_s'],event['representative_source_time_s'])
        for key, original in [('clip','event_000_full.mp4'),('roi_clip','event_000_roi.mp4'),
                              ('frame','event_000_objects.jpg'),('context','event_000_context.json')]:
            self.assertEqual(digest(self.root/'output'/event[key]), digest(source/'raw/event_000'/original))
        self.assertEqual(event['incident_context'],read(source/'raw/event_000/event_000_context.json'))
        self.assertFalse(event['class_mapping']['independent_training_metadata_verified'])

    def test_stored_b_separates_candidate_from_display_time(self):
        source=P3/'input_003-B/output'
        event=self.prepare('B',source=source)['index']['events'][0]
        self.assertEqual(event['candidate_time_s'],2.)
        self.assertAlmostEqual(event['display_sample_time_s'],6.153846153846153)
        self.assertEqual(event['representative_source_time_s'],6.16)

    def test_rejects_profile_missing_changed_or_mismatched(self):
        changed=profile('A'); changed['execution_settings']['batch_size']=1
        for snapshot in (None,changed,profile('B')):
            with self.subTest(snapshot=snapshot and snapshot.get('version')), self.assertRaises(ValueError):
                self.prepare(snapshot=snapshot)
        self.assertFalse((self.root/'output').exists())

    def test_rejects_wrong_input_and_untrusted_result_digest(self):
        for kwargs in ({'input_sha256':'0'*64},{'result_sha256':'0'*64}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): self.prepare(**kwargs)

    def test_rejects_changed_asset_missing_asset_and_symlink(self):
        for change in ('modified','missing','symlink'):
            with self.subTest(change=change):
                source=self.source(copy=True)
                path=source/'raw/evidence/event_000/frame.png'
                if change=='modified': path.write_bytes(path.read_bytes()+b'changed')
                else:
                    path.unlink()
                    if change=='symlink': path.symlink_to(self.source()/'raw/evidence/event_000/frame.png')
                with self.assertRaises(ValueError): self.prepare(source=source)
                shutil.rmtree(source)

    def test_rejects_unsafe_or_duplicate_artifact(self):
        for value in ('raw/../result.json','/tmp/foreign','raw//result.json','duplicate'):
            with self.subTest(value=value):
                source=self.source(copy=True); normalized=read(source/'result.json')
                if value=='duplicate': normalized['artifacts'].append(normalized['artifacts'][0])
                else: normalized['artifacts'][0]['path']=value
                save(source/'result.json',normalized)
                with self.assertRaises(ValueError): self.prepare(source=source)
                shutil.rmtree(source)

    def test_rejects_wrong_media_role_pts_count_or_codec(self):
        for field,value in [('role','roi_clip'),('pts_verified',False),('decoded_frames',99),('codec','mpeg4')]:
            with self.subTest(field=field):
                source=self.source(copy=True); normalized=read(source/'result.json')
                normalized['media'][0][field]=value;save(source/'result.json',normalized)
                with self.assertRaises(ValueError): self.prepare(source=source)
                shutil.rmtree(source)

    def test_rejects_b_context_or_handoff_drift_after_hashes_reseal(self):
        for name in ('raw/event_000/event_000_context.json','raw/vlm_handoffs.json'):
            with self.subTest(name=name):
                source=self.source('B',copy=True); data=read(source/name)
                context=data if isinstance(data,dict) else data[0]['incident_context']
                context['display_frame_time_s']=2.;save(source/name,data);reseal(source)
                with self.assertRaises(ValueError): self.prepare('B',source=source)
                shutil.rmtree(source)

    def test_no_candidates_requires_nonempty_complete_negative_windows(self):
        source=self.source(copy=True)
        raw=read(source/'raw/result.json');raw['events']=[];raw['vlm_handoffs']=[]
        for window in raw['x3d']['windows']:
            window['collision_probability']=0.;window['is_positive']=False
        raw['x3d']['decision']='Normal';raw['x3d']['max_collision_probability']=0.
        save(source/'raw/result.json',raw);save(source/'raw/vlm_handoffs.json',[])
        index=read(source/'raw/evidence.json');index['events']=[];save(source/'raw/evidence.json',index)
        shutil.rmtree(source/'raw/evidence')
        normalized=read(source/'result.json');normalized.update(status='no_candidates',event_count=0,
                                                               media=[],candidate_times_s=[])
        save(source/'result.json',normalized);reseal(source)
        result=self.prepare(source=source)
        self.assertEqual(result['index']['status'],'no_candidates')
        with self.assertRaises(ValueError): selected_event(result['index'],'event_000')
        raw['x3d']['windows']=[];save(source/'raw/result.json',raw);reseal(source)
        with self.assertRaises(ValueError): self.prepare(source=source,target=self.root/'invalid')

    def test_refuses_overwrite_and_source_nested_destination(self):
        self.prepare()
        before=digest(self.root/'output/manifest.json')
        with self.assertRaises(ValueError): self.prepare()
        self.assertEqual(before,digest(self.root/'output/manifest.json'))
        with self.assertRaises(ValueError): self.prepare(target=self.source()/'new')

    def test_validate_local_rejects_mutation_even_resealed_outer_manifest(self):
        self.prepare('B')
        root=self.root/'output'
        frame=root/'evidence/event_000/frame.jpg'
        frame.write_bytes(frame.read_bytes()+b'modified')
        manifest=read(root/'manifest.json');manifest['files']['evidence/event_000/frame.jpg']={
            'bytes':frame.stat().st_size,'sha256':digest(frame)}
        save(root/'manifest.json',manifest)
        with self.assertRaises(ValueError): validate_local(root)

    def test_validate_index_rejects_time_role_profile_and_fake_empty_objects(self):
        original=self.prepare('B')['index']
        for field,value in [('version','A'),('frame','../../foreign'),('candidate_time_s',float('nan')),
                            ('representative_source_time_s',1.4411764705882353),('frame_role','unannotated_frame'),
                            ('yolo_objects',[])]:
            index=deepcopy(original);index['events'][0][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError): validate_index(index)
        with self.assertRaises(ValueError):validate_index(original,snapshot=profile('A'))
        with self.assertRaises(ValueError):validate_index(original,input_sha256='0'*64)

    def test_rejects_coherent_changed_class_mapping_and_context_schema(self):
        for version in 'AB':
            source=self.source(version,copy=True)
            normalized=read(source/'result.json');runtime=read(source/'raw/runtime.json')
            normalized['class_mapping']['effective_names']['0']='wrong-class'
            runtime['class_mapping']=deepcopy(normalized['class_mapping'])
            save(source/'result.json',normalized);save(source/'raw/runtime.json',runtime);reseal(source)
            with self.subTest(version=version),self.assertRaises(ValueError):self.prepare(version,source=source)
            shutil.rmtree(source)
        index=self.prepare('B')['index']
        context=index['events'][0]['incident_context'];context['schema_version']='1.0'
        index['events'][0]['handoff']['incident_context']=deepcopy(context)
        with self.assertRaises(ValueError):validate_index(index)

    def test_validate_local_rejects_extra_file_missing_manifest_entry_and_symlink(self):
        for mode in ('extra','missing','symlink'):
            with self.subTest(mode=mode):
                root=self.root/mode;self.prepare(target=root)
                if mode=='extra': (root/'unlisted.txt').write_text('extra')
                elif mode=='symlink': (root/'unlisted.link').symlink_to(root/'runtime.json')
                else:
                    manifest=read(root/'manifest.json');manifest['files'].pop('raw/runtime.json')
                    save(root/'manifest.json',manifest)
                with self.assertRaises(ValueError): validate_local(root)


if __name__ == '__main__': unittest.main()
