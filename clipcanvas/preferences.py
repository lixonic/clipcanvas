"""Small, atomic desktop preferences; project edits stay in project files."""
import copy
import json
import os
from pathlib import Path
import tempfile


class Preferences:
    def __init__(self, directory=None):
        self.path = Path(directory or Path(os.environ.get('XDG_CONFIG_HOME', Path.home()/'.config'))/'clipcanvas')/'preferences.json'
        self.values = {}
        try:
            if self.path.stat().st_size <= 1024*1024:
                value = json.loads(self.path.read_text(encoding='utf-8'))
                if isinstance(value, dict): self.values = value
        except (OSError, ValueError, UnicodeError): pass

    def get(self, key, default=None):
        return copy.deepcopy(self.values.get(key, default))

    def set(self, key, value):
        candidate = {**self.values, key: copy.deepcopy(value)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.preferences-', dir=self.path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(candidate, stream, ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            self.values = candidate
        finally:
            Path(temporary).unlink(missing_ok=True)

    def folder(self, purpose):
        for key in ('folder_'+purpose, 'last_folder'):
            value = self.get(key)
            if isinstance(value, str) and Path(value).is_dir(): return value
        return str(Path.home())

    def remember_folder(self, purpose, folder):
        if folder and Path(folder).is_dir():
            self.set('folder_'+purpose, str(Path(folder).resolve()))
            self.set('last_folder', str(Path(folder).resolve()))
