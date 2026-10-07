# Plugins

Drop modular plugins here. Each plugin lives in its own subdirectory and exposes a standard interface.

## Convention

```
plugins/
└── your_plugin/
    ├── __init__.py
    └── plugin.py   # must export `register(app)`
```
