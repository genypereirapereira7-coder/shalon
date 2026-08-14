# PyInstaller: gera o `shalon-agente.exe` que roda no PC da cozinha.
#
#   pip install -e ".[empacotar]"
#   pyinstaller build.spec
#
# Um .exe único (`onefile`) porque quem instala isso não é programador: copia
# um arquivo pra pasta, põe o `config.ini` do lado e cria o atalho na inicial-
# ização do Windows. Sem Python instalado na máquina, sem venv, sem PATH.
#
# `console=True` de propósito: a janela preta com o log é o único jeito de
# alguém na loja ver "pedido #37 impresso" ou "não saiu papel" e conseguir
# mandar um print pra quem dá suporte.

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[("config.ini.exemplo", ".")],
    # Os drivers de impressora são importados dentro dos construtores (pra que
    # a falta de um não derrube o agente inteiro), e por isso o PyInstaller não
    # os enxerga sozinho na varredura de imports.
    hiddenimports=[
        "impressoras.fake",
        "impressoras.escpos_usb",
        "impressoras.escpos_rede",
        "impressoras.spooler_windows",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="shalon-agente",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
)
