from odoo import fields, models

from .core_backup import DEFAULT_DIR, DEFAULT_RETENTION_DAYS


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    core_backup_enabled = fields.Boolean(
        'Run scheduled backups', default=True,
        config_parameter='core_backup.enabled',
        help="Master switch. Off: the daily cron logs and does nothing.")
    core_backup_dir = fields.Char(
        'Backup directory', default=DEFAULT_DIR,
        config_parameter='core_backup.dir',
        help="Server path the archives are written to. Nothing outside it is "
             "ever deleted.")
    core_backup_retention_days = fields.Integer(
        'Keep backups for (days)', default=DEFAULT_RETENTION_DAYS,
        config_parameter='core_backup.retention_days')
    core_backup_with_filestore = fields.Boolean(
        'Include the filestore', default=True,
        config_parameter='core_backup.with_filestore',
        help="Off: the archive holds the database only, and attachments "
             "stored on disk are not in it.")
