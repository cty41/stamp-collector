from pathlib import Path
import webbrowser
APP=Path(__file__).resolve().parent
if __name__=='__main__':
    entry=APP/'index.html'
    if not entry.exists():entry=APP/'sample'/'index.html'
    webbrowser.open(entry.as_uri())
