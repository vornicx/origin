"""Skills del Cuerpo de Origin

Heavy skill imports are lazy to keep unit-test startup fast.
"""

__all__ = [
    "BaseSkill",
    "WebSearchSkill",
    "DateTimeSkill",
    "SystemInfoSkill",
    "CalculatorSkill",
    "WebScraperSkill",
    "ShellSkill",
    "ExternalAPIsSkill",
    "MemoryCompactionSkill",
    "FileManagerSkill",
    "CodeDoctorSkill",
    "VisionSkill",
    "UIAutomationSkill",
    "NotificationSkill",
    "MonitorSkill",
    "VoiceSkill",
    "AppIntegrationsSkill",
    "WakeWordSkill",
    "CameraSkill",
    "SystemTraySkill",
    "OSControlSkill",
    "ClipboardSkill",
    "ActiveWindowSkill",
]

_skill_map = {
    "BaseSkill": (".base_skill", "BaseSkill"),
    "WebSearchSkill": (".web_search", "WebSearchSkill"),
    "DateTimeSkill": (".datetime_skill", "DateTimeSkill"),
    "SystemInfoSkill": (".system_info", "SystemInfoSkill"),
    "CalculatorSkill": (".calculator", "CalculatorSkill"),
    "WebScraperSkill": (".web_scraper", "WebScraperSkill"),
    "ShellSkill": (".shell_skill", "ShellSkill"),
    "ExternalAPIsSkill": (".external_apis", "ExternalAPIsSkill"),
    "MemoryCompactionSkill": (".memory_compaction", "MemoryCompactionSkill"),
    "FileManagerSkill": (".file_manager", "FileManagerSkill"),
    "CodeDoctorSkill": (".code_doctor", "CodeDoctorSkill"),
    "VisionSkill": (".vision_skill", "VisionSkill"),
    "UIAutomationSkill": (".ui_automation_skill", "UIAutomationSkill"),
    "NotificationSkill": (".notification_skill", "NotificationSkill"),
    "MonitorSkill": (".monitor_skill", "MonitorSkill"),
    "VoiceSkill": (".voice_skill", "VoiceSkill"),
    "AppIntegrationsSkill": (".app_integrations", "AppIntegrationsSkill"),
    "WakeWordSkill": (".wake_word_skill", "WakeWordSkill"),
    "CameraSkill": (".camera_skill", "CameraSkill"),
    "SystemTraySkill": (".system_tray_skill", "SystemTraySkill"),
    "OSControlSkill": (".os_control_skill", "OSControlSkill"),
    "ClipboardSkill": (".clipboard_skill", "ClipboardSkill"),
    "ActiveWindowSkill": (".active_window_skill", "ActiveWindowSkill"),
}


def __getattr__(name: str):
    if name in _skill_map:
        import importlib
        mod_path, attr = _skill_map[name]
        mod = importlib.import_module(mod_path, package=__name__)
        return getattr(mod, attr)
    raise AttributeError(f"module 'skills' has no attribute {name!r}")
