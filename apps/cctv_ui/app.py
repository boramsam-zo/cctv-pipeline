"""Keep the established analysis tool; open the saved-result control room by default."""
import os
from pathlib import Path
import runpy

view = "analysis_app.py" if os.environ.get("CCTV_UI_VIEW") == "analysis" else "control_room.py"
runpy.run_path(str(Path(__file__).with_name(view)), run_name="__main__")
