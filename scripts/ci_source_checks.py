"""Run the public checkout's explicit synthetic CPU regression suite.

Tests use in-process API/DB/MLflow doubles and temporary synthetic files. Private
stored CCTV snapshots, provider calls, GPU/model execution and deployment are
outside this suite. Keep selection explicit: broad discovery includes historical
tests whose inputs deliberately are not published.
"""
import argparse
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GROUPS = {
    'report': (
        'tests.be_worker.test_report_continuation',
        'tests.be_worker.test_report_activation',
        'tests.be_worker.test_report_jobs',
        'tests.be_worker.test_report_review',
        'tests.be_worker.test_report_recovery',
        'tests.be_worker.test_report_recovery_view',
        'tests.be_worker.test_stored_selection_view',
        'tests.be_worker.test_hour_session_windows',
        'tests.be_worker.test_trial_limits',
        'tests.scene_description.test_team_scene_contract_cache',
    ),
    'mlflow': (
        'tests.be_worker.test_mlflow_tracking',
        'tests.be_worker.test_mlflow_tracing',
        'tests.be_worker.test_mlflow_trace_content',
        'tests.be_worker.test_mlflow_read',
        'tests.cctv_ui.test_demo_mlflow',
    ),
    'demo': (
        'tests.be_worker.test_demo_session',
        'tests.be_worker.test_demo_control',
        'tests.be_worker.test_demo_access',
        'tests.runpod.test_demo_start',
        'tests.cctv_ui.test_demo_boundary',
        'tests.cctv_ui.test_demo_gateway',
        'tests.cctv_ui.test_share_server',
        'tests.cctv_ui.test_share_gateway',
        'tests.cctv_ui.test_share_ui',
    ),
    'ui': (
        'tests.cctv_ui.test_app.AppTests.test_model_selection_keeps_full_report_request_and_does_not_auto_submit',
        'tests.cctv_ui.test_app.AppTests.test_same_filename_different_bytes_are_visible_in_rejection_receipt',
        'tests.cctv_ui.test_app.AppTests.test_unknown_admission_response_is_not_automatically_posted_again',
        'tests.cctv_ui.test_control_room.ControlRoomTests.test_empty_records_and_missing_vlm_are_not_normal_predictions',
        'tests.cctv_ui.test_report_revisions',
        'tests.cctv_ui.test_ui_snapshot',
        'tests.cctv_ui.test_demo_refresh',
        'tests.cctv_ui.test_demo_flow.AggregateTests',
        'tests.cctv_ui.test_demo_flow.LiveNoticeTests',
        'tests.cctv_ui.test_demo_flow.DemoAppTests.test_demo_analysis_defaults_full_report_and_hides_resume',
        'tests.cctv_ui.test_demo_flow.DemoAppTests.test_closed_entrypoint_does_not_read_jobs_or_register_pages',
        'tests.cctv_ui.test_demo_flow.DemoAppTests.test_single_entrypoint_navigates_control_analysis_and_mlflow',
        'tests.cctv_ui.test_demo_flow.DemoAppTests.test_live_panel_reuses_snapshot_and_notifies_only_for_verified_new_report',
        'tests.cctv_ui.test_demo_flow.DemoAppTests.test_upload_size_rechecked_before_submission',
        'tests.cctv_ui.test_demo_flow.DemoAppTests.test_only_unpaused_demo_gpu_poll_timeout_is_a_waiting_message',
    ),
    'privacy': ('tests.test_git_publication',),
    'rag': ('tests.team_rag.test_relevance_synthetic',),
}

# test_demo_driver reads a private operational artifact. The saved-snapshot
# demo_flow AppTest also needs unpublished CCTV data. Neither is selected here;
# their absence must not cause a broad discovery run to claim full coverage.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=tuple(GROUPS), action='append',
                        help='Run a selected group; repeat for multiple groups (default: all).')
    parser.add_argument('--list', action='store_true', help='Print selected test names without running.')
    args = parser.parse_args()
    names = [name for group in dict.fromkeys(args.group or GROUPS) for name in GROUPS[group]]
    if args.list:
        print('\n'.join(names))
        return 0
    suite = unittest.defaultTestLoader.loadTestsFromNames(names)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
