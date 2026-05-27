"""
Script de setup inicial de Origin.
Crea el entorno, instala dependencias y crea la BD.
"""
import subprocess
import sys
from pathlib import Path


def run(cmd: str, cwd: str = ".") -> int:
    print(f"\n>>> {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=cwd)
    return result.returncode


def main():
    root = Path(__file__).parent

    print("=" * 50)
    print("  Origin - Setup")
    print("=" * 50)

    # 1. Crea .env si no existe
    env_path = root / ".env"
    env_example = root / ".env.example"
    if not env_path.exists():
        print("\n[1/4] Creando .env desde .env.example...")
        env_path.write_text(env_example.read_text())
        print("      -> Edita .env con tus API keys antes de continuar.")
    else:
        print("\n[1/4] .env ya existe, skipping.")

    # 2. Instala dependencias Python
    print("\n[2/4] Instalando dependencias Python...")
    if run("pip install -r requirements.txt", cwd=str(root)) != 0:
        print("ERROR: Fallo al instalar dependencias")
        sys.exit(1)

    # 3. Corre migraciones
    print("\n[3/4] Corriendo migraciones de base de datos...")
    print("      -> Asegúrate de que PostgreSQL está corriendo y DATABASE_URL en .env es correcto.")
    ret = run("alembic upgrade head", cwd=str(root))
    if ret != 0:
        print("WARNING: Migración falló. Revisa la conexión a PostgreSQL.")
    else:
        print("      -> Migraciones OK.")

    # 4. Instala dependencias frontend
    print("\n[4/4] Instalando dependencias frontend...")
    if run("npm install", cwd=str(root / "frontend")) != 0:
        print("ERROR: Fallo al instalar dependencias frontend")
        sys.exit(1)

    print("\n" + "=" * 50)
    print("  Setup completo.")
    print()
    print("  Iniciar API:      uvicorn api.main:app --reload")
    print("  Iniciar Frontend: cd frontend && npm run dev")
    print("=" * 50)


if __name__ == "__main__":
    main()
