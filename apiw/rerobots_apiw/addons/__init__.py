"""add-ons


SCL <scott@rerobots>
Copyright (C) 2018-2022 rerobots, Inc.
"""

from .cam import (
    addon_cam_stream,
    apply_addon_cam,
    status_addon_cam,
    addon_cam_snapshot,
    addon_cam_stop_job,
    addon_cam_upload,
    remove_addon_cam,
)
from .cmd import (
    addon_cmd_stop_job,
    apply_addon_cmd,
    cmd_cancel_job,
    cmd_readline_stdout,
    cmd_rx_commands,
    cmd_send_command,
    cmd_send_file,
    remove_addon_cmd,
    status_addon_cmd,
)
from .drive import (
    addon_drive_stop_job,
    apply_addon_drive,
    drive_rx_commands,
    drive_send_command,
    remove_addon_drive,
    status_addon_drive,
)
from .mistyproxy import (
    apply_addon_mistyproxy,
    remove_addon_mistyproxy,
    status_addon_mistyproxy,
)
from .vnc import (
    addon_start_vnc,
    addon_stop_vnc,
    addon_vnc_stop_job,
    addon_vnc_waitdelete_job,
    apply_addon_vnc,
    remove_addon_vnc,
    status_addon_vnc,
)

from . import fulldevel
from . import minidevel


__all__ = [
    'addon_cam_snapshot',
    'addon_cam_stop_job',
    'addon_cam_stream',
    'addon_cam_upload',
    'addon_cmd_stop_job',
    'addon_drive_stop_job',
    'addon_start_vnc',
    'addon_stop_vnc',
    'addon_vnc_stop_job',
    'addon_vnc_waitdelete_job',
    'apply_addon_cam',
    'apply_addon_cmd',
    'apply_addon_drive',
    'apply_addon_mistyproxy',
    'apply_addon_vnc',
    'cmd_cancel_job',
    'cmd_readline_stdout',
    'cmd_rx_commands',
    'cmd_send_command',
    'cmd_send_file',
    'drive_rx_commands',
    'drive_send_command',
    'fulldevel',
    'minidevel',
    'remove_addon_cam',
    'remove_addon_cmd',
    'remove_addon_drive',
    'remove_addon_mistyproxy',
    'remove_addon_vnc',
    'status_addon_cam',
    'status_addon_cmd',
    'status_addon_drive',
    'status_addon_mistyproxy',
    'status_addon_vnc',
]
