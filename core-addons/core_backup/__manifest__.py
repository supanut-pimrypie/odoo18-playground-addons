{
    'name': 'Core Backup',
    'version': '18.0.1.0.0',
    'summary': 'Scheduled database + filestore backups to a directory on disk',
    'description': """
Core Backup
===========
* ``core.backup`` — one row per archive, with size, duration and error.
* Daily cron writes ``<db>_<timestamp>.zip`` into ``core_backup.dir``,
  laid out exactly like a native odoo backup: ``dump.sql``, ``filestore/``,
  ``manifest.json``.
* Backup > Backups — the archives, plus a **Backup now** button.
* Settings > Backup — master switch, directory, retention, filestore toggle.
* Retention only sweeps after a *successful* run, so failing backups never
  delete the last good ones.
* Backup only: this module never restores and never drops a database.
""",
    'category': 'Technical',
    'author': 'Supanut T.',
    'license': 'LGPL-3',
    # no core_api dependency on purpose: nothing here is about APIs, and the
    # one helper it used to import lives in odoo.tools as str2bool.
    'depends': ['base', 'base_setup'],
    'data': [
        'security/ir.model.access.csv',
        'data/core_backup_data.xml',
        'views/core_backup_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'installable': True,
    'application': False,
    'auto_install': False,
}
