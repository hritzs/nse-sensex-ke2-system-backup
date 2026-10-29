# Build on Windows using standalone/build_windows.ps1.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_all
root=Path(SPECPATH).parent
assets=root/"standalone/assets"
app=root/"whitegammaApp/whitegammaApp"
webdata,webbins,webhidden=collect_all("webview")
hidden=["standalone.worker","standalone.feed_runtime","standalone.supervisor",
        "standalone.inputs_refresh","standalone.diagnostics","standalone.failover","standalone.logging_utils","standalone.owned_job",
        "ke2_feed_server","ke2_test_api","gamma_weekly_server","gamma_weekly_engine",
        "sdk.examples.feed_client","server","engine","feed_client","inputs","black","synthetic","box","span",
        "numpy","numpy.polynomial.polynomial","pandas","openpyxl","tzdata"]
hidden+=collect_submodules("uvicorn")+webhidden
a=Analysis([str(root/"standalone/launcher.py")],pathex=[str(root),str(app)],
    binaries=webbins,datas=[(str(assets),"standalone_assets")]+webdata,
    hiddenimports=hidden,hookspath=[],hooksconfig={},runtime_hooks=[],
    excludes=["numba","llvmlite","matplotlib","IPython","pytest","tkinter"],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name="WhiteGammaStandalone",
    debug=False,bootloader_ignore_signals=False,strip=False,upx=False,
    console=False,disable_windowed_traceback=False,
    manifest=str(assets/"windows.manifest"),uac_admin=False)
