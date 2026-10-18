import sys

from importlib import reload

# Sublime namespaces package modules under the package name.
mod_prefix = 'SyncSettingsReborn.sync_settings_reborn'

# Python allows reloading modules on the fly, which allows us to do live upgrades.
# The only caveat is that you have to reload in the dependency order.
#
# Thus is module A depends on B and we don't reload B before A, when A is reloaded
# it will still have a reference to the old B. Thus we hard-code the dependency
# order of the various SyncSettingsReborn modules so they get reloaded properly.
#
# There are solutions for doing this all programmatically, but this is much easier
# to understand.
reload_mods = []
for mod in sys.modules:
    if mod.startswith(('sync_settings_reborn', 'SyncSettingsReborn')) and sys.modules[mod] is not None:
        reload_mods.append(mod)

mods_load_order = [
    '',

    '.libs',
    '.libs.logger',
    '.libs.path',
    '.libs.settings',
    '.libs.http',
    '.libs.gist',

    '.thread_progress',
    '.sync_version',
    '.sync_manager',
    '.auto_sync',

    '.commands',
    '.commands.decorators',
    '.commands.open_logs',
    '.commands.download',
    '.commands.upload',
    '.commands.delete',
]

for suffix in mods_load_order:
    mod = mod_prefix + suffix
    if mod in reload_mods:
        try:
            reload(sys.modules[mod])
        except ImportError:
            pass  # the module may no longer exist after an upgrade
