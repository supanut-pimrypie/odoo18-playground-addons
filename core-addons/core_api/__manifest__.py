{
    'name': 'Core API',
    'version': '18.0.1.0.0',
    'summary': 'Base layer for every API: logging, outbound client, log retention',
    'description': """
Base API
========
* ``core.api.log`` — one row per call, inbound and outbound, with payloads.
* ``@log_api`` decorator + ``json_response`` helper for inbound controllers.
* ``core.api.client._call()`` for logged outbound calls.
* ``core.api.endpoint`` — per-route on/off switch in the web UI, filled in by a
  route sweep on every install or upgrade.
* ``ir.module.module.api_enabled`` — switch off every route of one module.
* API > Dashboard — success/fail rates per time window and calls per module.
* Settings > API — master switches for inbound and outbound, plus retention.
* Monthly cron drops logs older than ``core_api.log_retention_days`` (default 30).
""",
    'category': 'Technical',
    'author': 'Supanut T.',
    'license': 'LGPL-3',
    'depends': ['base', 'base_setup'],
    'external_dependencies': {'python': ['requests', 'jsonschema']},
    'data': [
        'security/ir.model.access.csv',
        'data/core_api_data.xml',
        'views/api_log_views.xml',
        'views/api_dashboard_views.xml',
        'views/api_endpoint_views.xml',
        'views/ir_module_module_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'core_api/static/src/**/*',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
