# iOS / iPhone Support

Full CoProducer analysis (librosa + heavy native DSP) has limited support directly on stock iOS due to sandboxing and lack of full CPython + native wheels.

Recommended approaches (in priority):

1. Analyze on desktop (Windows/macOS) using the full installer and share files via iCloud / Files / AirDrop.
2. Use the portable Python package + a companion iOS app (Pythonista, Carnets, or a custom Swift + PythonKit build) for lightweight metadata + ffprobe style inspection.
3. For production: run analysis on Mac (or Windows) and embed the CoProducer* tags (via Mutagen) so the rich analysis metadata travels with the file and is readable on iPhone.

The core library code remains pure-Python + well-known wheels and can be packaged for future BeeWare / Kivy-iOS or Pyto style frontends.

See docs/INSTALLATION.md for desktop instructions and how analysis metadata is written into the audio tags.
