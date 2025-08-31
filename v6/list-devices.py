import sounddevice as sd # type: ignore
from pprint import pprint

pprint(sd.query_devices())
