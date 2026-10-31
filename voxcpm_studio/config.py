"""应用配置：读写 config.json，保存模型路径、生成参数等。"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

# 打包成 exe 后，模型/输出目录要贴着 exe 放，而不是塞进 _internal
if getattr(sys, "frozen", False):
    APP_ROOT = Path(sys.executable).resolve().parent
else:
    APP_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_ROOT / "config.json"
DEFAULT_MODEL_DIR = APP_ROOT / "models" / "VoxCPM2"
DEFAULT_OUTPUT_DIR = APP_ROOT / "outputs"

MODES = {
    "design": "语音设计（无需参考音频）",
    "clone": "声音克隆（参考音频）",
    "ultimate": "极限克隆（参考音频 + 参考文本）",
}


@dataclass
class AppConfig:
    # 模型
    model_dir: str = str(DEFAULT_MODEL_DIR)
    device: str = "auto"           # auto / cuda / cpu
    use_denoiser: bool = False     # ZipEnhancer 降噪模型（额外下载，默认关闭）
    optimize: bool = False         # torch.compile（Windows 无 triton，默认关闭）
    # 生成默认参数（质量优先：两项都取官方推荐区间上界，代价是慢约 2.2 倍）
    cfg_value: float = 3.0
    inference_timesteps: int = 30
    max_len: int = 4096
    min_len: int = 2
    normalize: bool = False
    denoise: bool = False
    retry_badcase: bool = True
    # 界面 / 输出
    theme: str = "light"           # light / dark（改动后重启生效）
    output_dir: str = str(DEFAULT_OUTPUT_DIR)
    auto_save: bool = True
    last_mode: str = "design"
    last_control: str = ""
    last_reference_dir: str = ""
    # 本地 API（给其它程序 / AI 调用）
    api_enabled: bool = False
    api_port: int = 8765
    api_token: str = ""            # 留空 = 只校验回环地址，不校验密钥

    # ---------------------------------------------------------------- #
    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        path = Path(path or CONFIG_PATH)
        cfg = cls()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return cfg
            known = {f.name for f in fields(cls)}
            for k, v in data.items():
                if k in known:
                    setattr(cfg, k, v)
        return cfg

    def save(self, path: Path | None = None) -> None:
        path = Path(path or CONFIG_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @property
    def model_ready(self) -> bool:
        d = Path(self.model_dir)
        return (d / "config.json").exists() and any(d.glob("*.safetensors"))
