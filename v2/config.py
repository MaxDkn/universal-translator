import toml
import threading

CONFIG_PATH = "config.toml"
_config_lock = threading.Lock()

class ConfigManager:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self.reload()

    def reload(self):
        with _config_lock:
            self._data = toml.load(self.path)

    def get(self, *keys):
        value = self._data
        for key in keys:
            value = value[key]
        return value

    def set(self, value, *keys):
        with _config_lock:
            d = self._data
            for key in keys[:-1]:
                d = d.setdefault(key, {})
            d[keys[-1]] = value
            with open(self.path, "w") as f:
                toml.dump(self._data, f)

    @property
    def gladia_key(self):
        return self.get("gladia_key")

    @property
    def agent_device(self):
        return self.get("devices", "agent")

    @property
    def phone_device(self):
        return self.get("devices", "phone")

    @property
    def agent_language(self):
        return self.get("languages", "agent")

    @property
    def phone_language(self):
        return self.get("languages", "phone")
    

config = ConfigManager()
