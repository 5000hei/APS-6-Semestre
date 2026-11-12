#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""COFRE VERDE — Autenticação Biométrica Facial (arquivo único).

COMO USAR: abra este arquivo no VS Code e clique em ▶ (Run Python File),
ou rode no terminal:  python cofre_verde.py

Na primeira execução ele instala sozinho o que faltar (pip) e depois abre o programa.
Os dados (banco, chave, logs, backups) ficam na pasta 'cofre_verde_dados' ao lado deste arquivo.
Requisitos: Python 3.10–3.12 (com tkinter) e uma webcam. Internet só na 1ª execução.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys


def _pausar() -> None:
    """Mantém a janela aberta para o usuário ler a mensagem (quando há terminal)."""
    try:
        if sys.stdin and sys.stdin.isatty():
            input("\nPressione Enter para sair...")
    except Exception:  # noqa: BLE001
        pass


def _tem(modulo: str) -> bool:
    try:
        importlib.import_module(modulo)
        return True
    except (Exception, SystemExit):  # face_recognition chama quit() se faltar modelos
        return False


def _pip(*pacotes: str) -> bool:
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *pacotes]
    print("  > " + " ".join(cmd[3:]))
    return subprocess.call(cmd) == 0


def _garantir_dependencias() -> None:
    """Instala automaticamente as bibliotecas que faltarem."""
    if os.environ.get("COFRE_VERDE_TESTE"):
        return
    if sys.version_info < (3, 10):
        print("Python 3.10 ou superior é necessário.")
        _pausar()
        sys.exit(1)
    if not _tem("tkinter"):
        print("O tkinter não está instalado neste Python.\n"
              "Windows: reinstale o Python marcando 'tcl/tk and IDLE'.\n"
              "Linux: sudo apt install python3-tk")
        _pausar()
        sys.exit(1)
    basicos = {"numpy": "numpy", "cv2": "opencv-python", "PIL": "Pillow", "customtkinter": "customtkinter",
               "matplotlib": "matplotlib", "reportlab": "reportlab", "cryptography": "cryptography"}
    faltam = [pkg for mod, pkg in basicos.items() if not _tem(mod)]
    if faltam:
        print("Instalando dependências (só na primeira vez):", ", ".join(faltam))
        if not _pip(*faltam):
            print("\nFalha ao instalar. Tente manualmente:  pip install " + " ".join(faltam))
            _pausar()
            sys.exit(1)
    if not _tem("face_recognition"):
        print("Instalando reconhecimento facial (dlib + face_recognition)...")
        _pip("setuptools<81", "Click", "face_recognition_models")
        if not _tem("dlib"):
            _pip("dlib-bin")
        _pip("face_recognition", "--no-deps")
        if not _tem("face_recognition"):
            print("\n[AVISO] Não foi possível instalar o face_recognition/dlib automaticamente.\n"
                  "O programa vai abrir, mas o reconhecimento só funciona depois de instalá-lo.\n"
                  "Dica: use Python 3.10, 3.11 ou 3.12 (o dlib-bin não tem pacote para versões mais novas).")
    importlib.invalidate_caches()


_garantir_dependencias()

import argparse
import csv
import io
import logging
import math
import queue
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile


# ============================================================================
# MÓDULO: config
# ============================================================================
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "cofre_verde_dados"
LOGS_DIR = DATA_DIR / "logs"
BACKUP_DIR = DATA_DIR / "backups"
DB_PATH = DATA_DIR / "cofre_verde.db"
CHAVE_PATH = DATA_DIR / "chave.key"

for _pasta in (DATA_DIR, LOGS_DIR, BACKUP_DIR):
    _pasta.mkdir(parents=True, exist_ok=True)

APP_NOME = "COFRE VERDE"
APP_SUBTITULO = "Autenticação Biométrica Facial"
APP_VERSAO = "1.0.0"


class Cores:
    """Paleta do tema escuro (preto-quente + roxo)."""

    FUNDO = "#1F1E1D"
    PAINEL = "#262624"
    BORDA = "#3A3936"
    TEXTO = "#ECEBE6"
    TEXTO_SEC = "#A8A69E"
    ROXO = "#6D28D9"
    ROXO_HOVER = "#7C3AED"
    ROXO_BRILHO = "#8B5CF6"
    ROXO_ESCURO = "#4C1D95"
    VERDE = "#22C55E"      # somente "acesso concedido"
    VERMELHO = "#EF4444"   # somente "acesso negado"
    ALERTA = "#F59E0B"     # avisos de fases/qualidade

    @staticmethod
    def bgr(cor_hex: str) -> tuple[int, int, int]:
        """Converte '#RRGGBB' para tupla BGR (formato do OpenCV)."""
        h = cor_hex.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (b, g, r)


NIVEIS_NOMES = {
    1: "Nível 1 — Permissão geral",
    2: "Nível 2 — Diretor de divisão",
    3: "Nível 3 — Ministro",
}
DIVISOES = [
    "Toxicologia Ambiental",
    "Agrotóxicos e Biocidas",
    "Resíduos Químicos",
    "Recursos Hídricos",
]
DIVISAO_GERAL = "Geral"
DIVISAO_MINISTRO = "Gabinete do Ministro"

# Parâmetros fixos do sistema
N_AMOSTRAS = 5
LIMITE_VIVACIDADE_S = 8.0      # tempo extra para o usuário piscar
MAX_TENTATIVAS = 3
BLOQUEIO_S = 60
SESSAO_TIMEOUT_S = 300
EAR_RAZAO = 0.75               # olho "fechado" se EAR < 75% do EAR aberto
EAR_FECHADO_ABS = 0.15
DISTANCIA_MAX_ENTRE_AMOSTRAS = 0.55


@dataclass(frozen=True)
class Ajustes:
    """Ajustes que o administrador pode alterar (persistidos no SQLite)."""

    tolerancia: float = 0.50        # distância euclidiana máxima p/ aceitar
    modelo_deteccao: str = "hog"    # "hog" ou "cnn"
    filtro: str = "bilateral"       # "bilateral" ou "gaussiano"
    tempo_estavel: float = 2.0      # segundos de rosto estável antes de decidir
    luz_min: float = 50.0
    luz_max: float = 215.0
    nitidez_min: float = 25.0


TEXTO_LGPD = """AVISO DE PRIVACIDADE E CONSENTIMENTO (LGPD — Lei nº 13.709/2018)

1. Finalidade: os dados faciais são tratados exclusivamente para identificar e autenticar pessoas autorizadas a acessar o cofre de informações do Ministério do Meio Ambiente, e para manter a auditoria de segurança.

2. Dados coletados: nome, cargo, nível de acesso, divisão, representação matemática do rosto (vetor de 128 números, não a foto original) e uma miniatura do rosto no cadastro. Em cada tentativa de acesso é registrada a captura do rosto, data/hora e resultado.

3. Dados biométricos são dados pessoais sensíveis (art. 11). O tratamento se baseia no consentimento do titular e na garantia da segurança do cofre.

4. Armazenamento: todos os dados ficam APENAS neste computador (SQLite local). Vetores faciais, miniaturas e capturas são criptografados (Fernet/AES). Nenhum dado é enviado pela internet.

5. Acesso: somente o Ministro (nível 3) pode listar, editar, remover usuários e consultar os logs de auditoria completos.

6. Direitos do titular: confirmação do tratamento, acesso, correção e eliminação dos dados, bem como revogação do consentimento, mediante solicitação ao administrador do sistema (a remoção do usuário apaga o vetor facial e a miniatura).

7. Retenção: os dados cadastrais são mantidos enquanto o usuário tiver acesso ao cofre; os logs de auditoria são mantidos para fins de segurança.

Ao marcar a caixa de consentimento, você declara ter lido e compreendido este aviso e concorda com o tratamento descrito."""


# ============================================================================
# MÓDULO: database.crypto
# ============================================================================
import logging
import os
from pathlib import Path

import numpy as np
from cryptography.fernet import Fernet, InvalidToken

log = logging.getLogger(__name__)


class ErroCriptografia(Exception):
    """Falha ao cifrar/decifrar (chave ausente, trocada ou dado corrompido)."""


class Cripto:
    """Gerencia a chave Fernet local e cifra/decifra bytes."""

    def __init__(self, caminho_chave: Path) -> None:
        self.caminho = Path(caminho_chave)
        self._fernet: Fernet | None = None
        self.carregar()

    def carregar(self) -> None:
        """Carrega a chave do disco (cria uma nova se não existir)."""
        try:
            if not self.caminho.exists():
                self.caminho.parent.mkdir(parents=True, exist_ok=True)
                self.caminho.write_bytes(Fernet.generate_key())
                try:
                    os.chmod(self.caminho, 0o600)
                except OSError:
                    pass
                log.info("Nova chave de criptografia gerada.")
            self._fernet = Fernet(self.caminho.read_bytes().strip())
        except (OSError, ValueError) as exc:
            raise ErroCriptografia(f"Não foi possível carregar a chave de criptografia: {exc}") from exc

    def cifrar(self, dados: bytes) -> bytes:
        """Cifra bytes com Fernet."""
        assert self._fernet is not None
        return self._fernet.encrypt(dados)

    def decifrar(self, token: bytes) -> bytes:
        """Decifra bytes; lança ErroCriptografia se inválido."""
        assert self._fernet is not None
        try:
            return self._fernet.decrypt(bytes(token))
        except (InvalidToken, ValueError, TypeError) as exc:
            raise ErroCriptografia("Dado criptografado inválido ou chave incorreta.") from exc


def encoding_para_bytes(encoding: np.ndarray) -> bytes:
    """Serializa o vetor facial (128 floats) em bytes."""
    return np.asarray(encoding, dtype=np.float64).tobytes()


def bytes_para_encoding(dados: bytes) -> np.ndarray:
    """Reconstrói o vetor facial a partir dos bytes."""
    vetor = np.frombuffer(dados, dtype=np.float64).copy()
    if vetor.shape[0] != 128:
        raise ErroCriptografia("Vetor facial com tamanho inesperado.")
    return vetor


# ============================================================================
# MÓDULO: database.documentos
# ============================================================================
from dataclasses import dataclass


AVISO = "\n\n[Documento fictício criado para fins acadêmicos.]"


@dataclass(frozen=True)
class Documento:
    """Documento do cofre."""

    id: str
    titulo: str
    nivel: int
    divisao: str  # "" = não vinculado a divisão
    conteudo: str


DOCUMENTOS: list[Documento] = [
    Documento("N1-01", "Protocolo geral de segurança do cofre", 1, "",
              "Regras de conduta: acesso individual, proibido tailgating, registro obrigatório de toda entrada e saída. "
              "Em caso de falha do sistema biométrico, acionar a Coordenação de Segurança." + AVISO),
    Documento("N1-02", "Lista pública de substâncias monitoradas", 1, "",
              "Relação genérica das categorias monitoradas pelo Ministério: metais pesados, biotoxinas aquáticas, "
              "resíduos industriais e biocidas. Sem quantitativos ou localizações." + AVISO),
    Documento("N1-03", "Procedimentos de emergência e evacuação", 1, "",
              "Rotas de saída, pontos de encontro e telefones internos. Simulados trimestrais obrigatórios." + AVISO),
    Documento("N2-TA", "Relatório sintético — biotoxinas em bacias (Toxicologia Ambiental)", 2, DIVISOES[0],
              "Série histórica simulada de monitoramento de cianotoxinas em reservatórios fictícios. "
              "Tendência de queda de 12% no último semestre (dados simulados)." + AVISO),
    Documento("N2-AB", "Dossiê de avaliação de biocidas (Agrotóxicos e Biocidas)", 2, DIVISOES[1],
              "Parecer técnico fictício sobre reavaliação de três princípios ativos hipotéticos, "
              "com cronograma de consulta pública." + AVISO),
    Documento("N2-RQ", "Mapa de passivos ambientais (Resíduos Químicos)", 2, DIVISOES[2],
              "Inventário simulado de áreas contaminadas em reabilitação, com prioridades de remediação." + AVISO),
    Documento("N2-RH", "Plano de monitoramento hídrico (Recursos Hídricos)", 2, DIVISOES[3],
              "Pontos de coleta fictícios, periodicidade e indicadores de qualidade da água." + AVISO),
    Documento("N3-01", "Plano nacional de contingência toxicológica", 3, "",
              "Fluxos de decisão e cadeia de comando para incidentes ambientais de grande escala (conteúdo simulado)." + AVISO),
    Documento("N3-02", "Inventário consolidado de toxinas de uso restrito", 3, "",
              "Índice consolidado fictício das divisões, visível apenas ao Ministro." + AVISO),
    Documento("N3-03", "Ata reservada do comitê de segurança", 3, "",
              "Deliberações simuladas sobre auditoria do cofre e revisão de credenciais." + AVISO),
]


def pode_acessar(doc: Documento, nivel: int, divisao: str) -> tuple[bool, str]:
    """Regra de acesso: N1 todos; N2 só a própria divisão; N3 só o ministro."""
    if doc.nivel == 1:
        return True, ""
    if doc.nivel == 2:
        if nivel >= 3 or (nivel == 2 and divisao.strip().lower() == doc.divisao.lower()):
            return True, ""
        if nivel == 2:
            return False, f"Documento de outra divisão ({doc.divisao})."
        return False, "Documento de nível 2 (diretores de divisão)."
    if nivel >= 3:
        return True, ""
    return False, "Documento exclusivo do Ministro (nível 3)."


def documentos_visiveis(nivel: int, divisao: str) -> list[Documento]:
    """Retorna somente os documentos que o nível/divisão pode abrir."""
    return [d for d in DOCUMENTOS if pode_acessar(d, nivel, divisao)[0]]


# ============================================================================
# MÓDULO: database.db
# ============================================================================
import logging
import shutil
import sqlite3
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np


log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nome TEXT NOT NULL COLLATE NOCASE UNIQUE,
    cargo TEXT NOT NULL,
    nivel INTEGER NOT NULL CHECK (nivel BETWEEN 1 AND 3),
    divisao TEXT NOT NULL DEFAULT '',
    encoding BLOB NOT NULL,
    foto BLOB,
    consentimento_em TEXT NOT NULL,
    criado_em TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ministro ON usuarios(nivel) WHERE nivel = 3;
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    data_hora TEXT NOT NULL,
    usuario_id INTEGER,
    usuario_nome TEXT NOT NULL,
    resultado TEXT NOT NULL,
    nivel_solicitado INTEGER,
    confianca REAL,
    motivo TEXT NOT NULL DEFAULT '',
    captura BLOB
);
CREATE INDEX IF NOT EXISTS idx_logs_data ON logs(data_hora);
CREATE TABLE IF NOT EXISTS config (chave TEXT PRIMARY KEY, valor TEXT NOT NULL);
"""


class ErroBanco(Exception):
    """Erro genérico de banco de dados, com mensagem amigável em português."""


class UsuarioDuplicado(ErroBanco):
    """Já existe usuário com o mesmo nome."""


class MinistroJaExiste(ErroBanco):
    """Só pode existir 1 usuário nível 3."""


@dataclass
class Usuario:
    """Usuário cadastrado."""

    id: int
    nome: str
    cargo: str
    nivel: int
    divisao: str
    criado_em: str
    encoding: Optional[np.ndarray] = None


def agora_str() -> str:
    """Data/hora local no formato ISO simples."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Banco:
    """Acesso thread-safe ao SQLite."""

    def __init__(self, caminho: Path, cripto: Cripto) -> None:
        self.caminho = Path(caminho)
        self.cripto = cripto
        self._lock = threading.RLock()
        self.aviso_recuperacao = ""
        self.con: sqlite3.Connection
        try:
            self._conectar()
        except sqlite3.DatabaseError as exc:
            self._recuperar(exc)

    # ---------------------------------------------------------------- conexão
    def _conectar(self) -> None:
        con = sqlite3.connect(str(self.caminho), check_same_thread=False, timeout=10)
        try:
            con.row_factory = sqlite3.Row
            linha = con.execute("PRAGMA integrity_check").fetchone()
            if linha is None or linha[0] != "ok":
                raise sqlite3.DatabaseError(f"verificação de integridade falhou ({linha[0] if linha else '?'})")
            con.executescript(SCHEMA)
            con.commit()
        except Exception:
            con.close()
            raise
        self.con = con

    def _recuperar(self, exc: Exception) -> None:
        """Banco corrompido: preserva o arquivo e cria um novo, sem travar."""
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        destino = self.caminho.parent / f"{self.caminho.stem}.corrompido_{ts}.db"
        try:
            shutil.move(str(self.caminho), str(destino))
        except OSError as erro_mv:
            log.error("Não foi possível mover banco corrompido: %s", erro_mv)
        self._conectar()
        self.aviso_recuperacao = (
            f"O banco de dados estava corrompido ({exc}). O arquivo foi preservado como "
            f"'{destino.name}' e um banco novo foi criado. Cadastre o Ministro novamente ou restaure um backup."
        )
        log.error(self.aviso_recuperacao)

    def fechar(self) -> None:
        """Fecha a conexão."""
        with self._lock:
            try:
                self.con.close()
            except sqlite3.Error:
                pass

    # ------------------------------------------------------------------ config
    def obter_config(self, chave: str, padrao: str = "") -> str:
        with self._lock:
            linha = self.con.execute("SELECT valor FROM config WHERE chave = ?", (chave,)).fetchone()
        return linha["valor"] if linha else padrao

    def definir_config(self, chave: str, valor: Any) -> None:
        with self._lock:
            self.con.execute("INSERT INTO config(chave, valor) VALUES(?, ?) "
                             "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor", (chave, str(valor)))
            self.con.commit()

    def carregar_ajustes(self) -> Ajustes:
        """Lê os ajustes do administrador (com valores padrão)."""
        padrao = Ajustes()

        def _f(chave: str, p: float) -> float:
            try:
                return float(self.obter_config(chave, str(p)))
            except ValueError:
                return p

        modelo = self.obter_config("modelo_deteccao", padrao.modelo_deteccao)
        filtro = self.obter_config("filtro", padrao.filtro)
        return Ajustes(
            tolerancia=min(0.8, max(0.3, _f("tolerancia", padrao.tolerancia))),
            modelo_deteccao=modelo if modelo in ("hog", "cnn") else padrao.modelo_deteccao,
            filtro=filtro if filtro in ("bilateral", "gaussiano") else padrao.filtro,
            tempo_estavel=min(5.0, max(1.0, _f("tempo_estavel", padrao.tempo_estavel))),
            luz_min=_f("luz_min", padrao.luz_min),
            luz_max=_f("luz_max", padrao.luz_max),
            nitidez_min=_f("nitidez_min", padrao.nitidez_min),
        )

    def salvar_ajustes(self, a: Ajustes) -> None:
        """Persiste os ajustes."""
        for chave in ("tolerancia", "modelo_deteccao", "filtro", "tempo_estavel", "luz_min", "luz_max", "nitidez_min"):
            self.definir_config(chave, getattr(a, chave))

    # ---------------------------------------------------------------- usuários
    def contar_usuarios(self) -> int:
        with self._lock:
            return int(self.con.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0])

    def existe_ministro(self) -> bool:
        with self._lock:
            return self.con.execute("SELECT 1 FROM usuarios WHERE nivel = 3").fetchone() is not None

    def criar_usuario(self, nome: str, cargo: str, nivel: int, divisao: str,
                      encoding: np.ndarray, foto_jpg: Optional[bytes]) -> int:
        """Cadastra usuário com encoding e foto criptografados."""
        nome, cargo = nome.strip(), cargo.strip()
        with self._lock:
            if self.con.execute("SELECT 1 FROM usuarios WHERE nome = ? COLLATE NOCASE", (nome,)).fetchone():
                raise UsuarioDuplicado(f"Já existe um usuário chamado '{nome}'.")
            if nivel == 3 and self.existe_ministro():
                raise MinistroJaExiste("Já existe um Ministro (nível 3). Só pode haver um usuário nível 3.")
            try:
                cur = self.con.execute(
                    "INSERT INTO usuarios(nome, cargo, nivel, divisao, encoding, foto, consentimento_em, criado_em) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (nome, cargo, nivel, divisao, self.cripto.cifrar(encoding_para_bytes(encoding)),
                     self.cripto.cifrar(foto_jpg) if foto_jpg else None, agora_str(), agora_str()))
                self.con.commit()
                return int(cur.lastrowid)
            except sqlite3.DatabaseError as exc:
                self.con.rollback()
                raise ErroBanco(f"Falha ao gravar usuário: {exc}") from exc

    def _linha_para_usuario(self, r: sqlite3.Row, com_encoding: bool) -> Usuario:
        enc = bytes_para_encoding(self.cripto.decifrar(r["encoding"])) if com_encoding else None
        return Usuario(r["id"], r["nome"], r["cargo"], r["nivel"], r["divisao"], r["criado_em"], enc)

    def listar_usuarios(self, com_encoding: bool = False) -> list[Usuario]:
        """Lista usuários; usuários com encoding ilegível são ignorados (e registrados em log)."""
        with self._lock:
            linhas = self.con.execute("SELECT * FROM usuarios ORDER BY nivel DESC, nome").fetchall()
        saida: list[Usuario] = []
        for r in linhas:
            try:
                saida.append(self._linha_para_usuario(r, com_encoding))
            except ErroCriptografia as exc:
                log.error("Usuário %s ignorado: %s", r["nome"], exc)
        return saida

    def obter_usuario(self, usuario_id: int) -> Optional[Usuario]:
        with self._lock:
            r = self.con.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
        return self._linha_para_usuario(r, False) if r else None

    def obter_foto(self, usuario_id: int) -> Optional[bytes]:
        with self._lock:
            r = self.con.execute("SELECT foto FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
        if not r or r["foto"] is None:
            return None
        try:
            return self.cripto.decifrar(r["foto"])
        except ErroCriptografia:
            return None

    def atualizar_usuario(self, usuario_id: int, nome: str, cargo: str, nivel: int, divisao: str) -> None:
        """Edita dados cadastrais respeitando as regras do nível 3."""
        nome, cargo = nome.strip(), cargo.strip()
        atual = self.obter_usuario(usuario_id)
        if atual is None:
            raise ErroBanco("Usuário não encontrado.")
        with self._lock:
            if self.con.execute("SELECT 1 FROM usuarios WHERE nome = ? COLLATE NOCASE AND id <> ?",
                                (nome, usuario_id)).fetchone():
                raise UsuarioDuplicado(f"Já existe um usuário chamado '{nome}'.")
            if atual.nivel == 3 and nivel != 3:
                raise ErroBanco("O Ministro (nível 3) não pode ser rebaixado: o sistema ficaria sem administrador.")
            if nivel == 3 and atual.nivel != 3 and self.existe_ministro():
                raise MinistroJaExiste("Já existe um Ministro (nível 3). Só pode haver um usuário nível 3.")
            self.con.execute("UPDATE usuarios SET nome=?, cargo=?, nivel=?, divisao=? WHERE id=?",
                             (nome, cargo, nivel, divisao, usuario_id))
            self.con.commit()

    def remover_usuario(self, usuario_id: int) -> None:
        """Remove usuário (apaga encoding e foto). O Ministro não pode ser removido pela interface."""
        atual = self.obter_usuario(usuario_id)
        if atual is None:
            raise ErroBanco("Usuário não encontrado.")
        if atual.nivel == 3:
            raise ErroBanco("O Ministro (nível 3) não pode ser removido pela interface. "
                            "Use 'python main.py --redefinir-ministro' no computador do cofre.")
        with self._lock:
            self.con.execute("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
            self.con.commit()

    def remover_ministro(self) -> int:
        """Uso exclusivo da linha de comando (acesso físico). Retorna linhas removidas."""
        with self._lock:
            cur = self.con.execute("DELETE FROM usuarios WHERE nivel = 3")
            self.con.commit()
            return cur.rowcount

    # -------------------------------------------------------------------- logs
    def registrar_log(self, usuario_id: Optional[int], usuario_nome: str, resultado: str,
                      nivel_solicitado: Optional[int], confianca: Optional[float], motivo: str,
                      captura_jpg: Optional[bytes] = None) -> None:
        """Grava evento de auditoria (a captura do rosto é criptografada)."""
        try:
            captura = self.cripto.cifrar(captura_jpg) if captura_jpg else None
            with self._lock:
                self.con.execute(
                    "INSERT INTO logs(data_hora, usuario_id, usuario_nome, resultado, nivel_solicitado, "
                    "confianca, motivo, captura) VALUES(?,?,?,?,?,?,?,?)",
                    (agora_str(), usuario_id, usuario_nome, resultado, nivel_solicitado, confianca, motivo, captura))
                self.con.commit()
        except (sqlite3.DatabaseError, ErroCriptografia) as exc:
            log.error("Falha ao registrar log de auditoria: %s", exc)

    def listar_logs(self, resultado: Optional[str] = None, busca: str = "", limite: int = 500) -> list[sqlite3.Row]:
        """Lista logs (mais recentes primeiro). resultado: CONCEDIDO, NEGADO, OUTROS ou None."""
        sql = ("SELECT id, data_hora, usuario_id, usuario_nome, resultado, nivel_solicitado, confianca, motivo, "
               "captura IS NOT NULL AS tem_captura FROM logs WHERE 1=1")
        params: list[Any] = []
        if resultado in ("CONCEDIDO", "NEGADO"):
            sql += " AND resultado = ?"
            params.append(resultado)
        elif resultado == "OUTROS":
            sql += " AND resultado NOT IN ('CONCEDIDO','NEGADO')"
        if busca.strip():
            sql += " AND (usuario_nome LIKE ? OR motivo LIKE ?)"
            params += [f"%{busca.strip()}%"] * 2
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limite)
        with self._lock:
            return self.con.execute(sql, params).fetchall()

    def obter_captura(self, log_id: int) -> Optional[bytes]:
        with self._lock:
            r = self.con.execute("SELECT captura FROM logs WHERE id = ?", (log_id,)).fetchone()
        if not r or r["captura"] is None:
            return None
        try:
            return self.cripto.decifrar(r["captura"])
        except ErroCriptografia:
            return None

    def executar_consulta(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """Consulta de leitura (usada pelos relatórios)."""
        with self._lock:
            return self.con.execute(sql, params).fetchall()

    # ------------------------------------------------------------------ backup
    def exportar_backup(self, destino: Path) -> None:
        """Gera um .zip com o banco (cópia consistente) e a chave de criptografia."""
        tmp = Path(tempfile.mkdtemp())
        try:
            tmp_db = tmp / "cofre_verde.db"
            with self._lock:
                dest = sqlite3.connect(str(tmp_db))
                try:
                    self.con.backup(dest)
                finally:
                    dest.close()
            with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(tmp_db, "cofre_verde.db")
                z.write(self.cripto.caminho, "chave.key")
        except (OSError, sqlite3.Error) as exc:
            raise ErroBanco(f"Falha ao gerar o backup: {exc}") from exc
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def restaurar_backup(self, origem: Path) -> None:
        """Valida e restaura um backup (.zip). Mantém cópia de segurança do estado atual."""
        tmp = Path(tempfile.mkdtemp())
        try:
            try:
                with zipfile.ZipFile(origem) as z:
                    if not {"cofre_verde.db", "chave.key"} <= set(z.namelist()):
                        raise ErroBanco("Arquivo de backup inválido (faltam cofre_verde.db e/ou chave.key).")
                    z.extract("cofre_verde.db", tmp)
                    z.extract("chave.key", tmp)
            except zipfile.BadZipFile as exc:
                raise ErroBanco("O arquivo selecionado não é um backup válido (.zip).") from exc
            try:
                con = sqlite3.connect(str(tmp / "cofre_verde.db"))
                try:
                    ok = con.execute("PRAGMA integrity_check").fetchone()[0]
                    tabelas = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    amostra = con.execute("SELECT encoding FROM usuarios LIMIT 1").fetchone()
                finally:
                    con.close()
                if ok != "ok" or not {"usuarios", "logs", "config"} <= tabelas:
                    raise ErroBanco("O banco dentro do backup está corrompido ou incompleto.")
                if amostra is not None:
                    Cripto(tmp / "chave.key").decifrar(amostra[0])
            except (sqlite3.Error, ErroCriptografia) as exc:
                raise ErroBanco(f"Backup inválido: {exc}") from exc
            with self._lock:
                self.con.close()
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                try:
                    if self.caminho.exists():
                        shutil.copy2(self.caminho, BACKUP_DIR / f"antes_restauracao_{ts}.db")
                    if self.cripto.caminho.exists():
                        shutil.copy2(self.cripto.caminho, BACKUP_DIR / f"antes_restauracao_{ts}.key")
                    shutil.copy2(tmp / "cofre_verde.db", self.caminho)
                    shutil.copy2(tmp / "chave.key", self.cripto.caminho)
                    self.cripto.carregar()
                finally:
                    self._conectar()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ============================================================================
# MÓDULO: pipeline.modelos
# ============================================================================
import logging

import numpy as np

log = logging.getLogger(__name__)

ERRO_IMPORTACAO = ""
try:  # face_recognition chama quit() se faltar face_recognition_models -> captura SystemExit
    import face_recognition  # type: ignore
except (Exception, SystemExit) as _exc:  # noqa: BLE001
    face_recognition = None  # type: ignore
    ERRO_IMPORTACAO = str(_exc) or _exc.__class__.__name__


def verificar_modelos() -> tuple[bool, str]:
    """Testa se dlib/face_recognition e os modelos (HOG, landmarks, ResNet) carregam."""
    if face_recognition is None:
        return False, ("Não foi possível carregar a biblioteca face_recognition/dlib. "
                       "Siga as instruções de instalação do README (seção dlib no Windows). "
                       f"Detalhe: {ERRO_IMPORTACAO}")
    try:
        img = np.zeros((64, 64, 3), dtype=np.uint8)
        caixa = [(0, 63, 63, 0)]
        face_recognition.face_locations(img)
        face_recognition.face_landmarks(img, caixa)
        face_recognition.face_encodings(img, caixa)
        return True, "Modelos de reconhecimento facial carregados."
    except Exception as exc:  # noqa: BLE001
        log.exception("Falha ao carregar modelos")
        return False, f"Falha ao carregar os modelos de reconhecimento facial: {exc}"


# ============================================================================
# MÓDULO: pipeline.aquisicao
# ============================================================================
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)


class ErroImagem(Exception):
    """Arquivo de imagem inválido ou ilegível."""


def carregar_imagem(caminho: str | Path) -> np.ndarray:
    """Carrega imagem de arquivo (suporta caminhos com acento) e limita o tamanho a 1000 px."""
    try:
        dados = np.fromfile(str(caminho), dtype=np.uint8)
    except OSError as exc:
        raise ErroImagem(f"Não foi possível ler o arquivo: {exc}") from exc
    img = cv2.imdecode(dados, cv2.IMREAD_COLOR) if dados.size else None
    if img is None:
        raise ErroImagem("O arquivo selecionado não é uma imagem válida (use JPG, PNG ou BMP).")
    maior = max(img.shape[:2])
    if maior > 1000:
        f = 1000 / maior
        img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    return img


class Camera:
    """Captura contínua em thread separada; a GUI apenas lê o último quadro."""

    def __init__(self, indice: int = 0, largura: int = 640, altura: int = 480) -> None:
        self.indice, self.largura, self.altura = indice, largura, altura
        self._cap: Optional[cv2.VideoCapture] = None
        self._thread: Optional[threading.Thread] = None
        self._parar = threading.Event()
        self._lock = threading.Lock()
        self._frame: Optional[np.ndarray] = None
        self._id = 0
        self.erro = "Câmera ainda não iniciada."
        self.ativa = False
        self.fps = 0.0

    def iniciar(self) -> tuple[bool, str]:
        """Abre a câmera e inicia a thread de captura. Retorna (sucesso, mensagem)."""
        self.parar()
        ok, msg = self._abrir()
        if not ok:
            self.erro = msg
            return False, msg
        self.erro = ""
        self._parar.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="CameraThread")
        self._thread.start()
        return True, "Câmera ativa."

    def _abrir(self) -> tuple[bool, str]:
        try:
            cap = cv2.VideoCapture(self.indice, cv2.CAP_DSHOW) if sys.platform.startswith("win") \
                else cv2.VideoCapture(self.indice)
        except Exception as exc:  # noqa: BLE001
            return False, f"Erro ao acessar a câmera: {exc}"
        if cap is None or not cap.isOpened():
            if cap is not None:
                cap.release()
            return False, ("Câmera não encontrada ou em uso por outro aplicativo. Conecte uma webcam, "
                           "feche programas que a utilizem (Zoom, Teams...) e clique em 'Reiniciar câmera'.")
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.largura)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.altura)
        for _ in range(8):
            ok, frame = cap.read()
            if ok and frame is not None:
                self._cap = cap
                return True, ""
            time.sleep(0.1)
        cap.release()
        return False, "A câmera abriu, mas não envia imagens (possivelmente ocupada por outro programa)."

    def _reconectar(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        while not self._parar.is_set():
            time.sleep(2.0)
            ok, _ = self._abrir()
            if ok:
                self.erro = ""
                return

    def _loop(self) -> None:
        falhas, anterior = 0, time.perf_counter()
        while not self._parar.is_set():
            try:
                ok, frame = self._cap.read() if self._cap is not None else (False, None)
            except Exception as exc:  # noqa: BLE001
                log.error("Erro de leitura da câmera: %s", exc)
                ok, frame = False, None
            if not ok or frame is None:
                falhas += 1
                if falhas >= 30:
                    self.ativa = False
                    self.erro = "Conexão com a câmera perdida. Tentando reconectar..."
                    self._reconectar()
                    falhas = 0
                time.sleep(0.03)
                continue
            falhas = 0
            self.ativa = True
            with self._lock:
                self._frame = frame
                self._id += 1
            agora = time.perf_counter()
            dt = max(agora - anterior, 1e-3)
            anterior = agora
            self.fps = 0.9 * self.fps + 0.1 * (1.0 / dt) if self.fps else 1.0 / dt

    def ler_com_id(self) -> tuple[Optional[np.ndarray], int]:
        """Último quadro (BGR) e seu contador. NÃO modifique o array retornado: copie antes."""
        with self._lock:
            return self._frame, self._id

    def parar(self) -> None:
        """Encerra a captura e libera a câmera."""
        self._parar.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self.ativa = False
        with self._lock:
            self._frame = None


# ============================================================================
# MÓDULO: pipeline.preprocessamento
# ============================================================================
import math
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class ResultadoPreproc:
    """Imagens intermediárias da fase 2 (para exibir antes/depois)."""

    cinza: np.ndarray        # escala de cinza
    clahe: np.ndarray        # cinza com equalização adaptativa
    filtrado: np.ndarray     # cinza + CLAHE + filtro de ruído
    normalizado: np.ndarray  # colorida (BGR) com iluminação normalizada e ruído reduzido
    luminosidade: float      # média do cinza original (0–255)


def medir_luminosidade(cinza: np.ndarray) -> float:
    """Luminosidade média da imagem (0 = preto, 255 = branco)."""
    return float(cinza.mean())


def medir_nitidez(rosto_bgr: np.ndarray) -> float:
    """Nitidez = variância do Laplaciano do rosto redimensionado para 160x160 (baixo = borrado)."""
    cinza = cv2.cvtColor(rosto_bgr, cv2.COLOR_BGR2GRAY)
    cinza = cv2.resize(cinza, (160, 160), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(cinza, cv2.CV_64F).var())


def _filtrar(img: np.ndarray, filtro: str) -> np.ndarray:
    if filtro == "gaussiano":
        return cv2.GaussianBlur(img, (5, 5), 0)
    return cv2.bilateralFilter(img, 5, 40, 40)  # preserva bordas


def _corrigir_gamma(canal_l: np.ndarray) -> np.ndarray:
    """Leva a luminosidade média do canal L para ~50% (normalização de iluminação)."""
    media = float(canal_l.mean()) / 255.0
    if 0.4 <= media <= 0.6:
        return canal_l
    media = min(max(media, 0.05), 0.95)
    gama = min(max(math.log(0.5) / math.log(media), 0.5), 2.0)
    tabela = (np.power(np.arange(256) / 255.0, gama) * 255).astype(np.uint8)
    return cv2.LUT(canal_l, tabela)


def preprocessar(frame_bgr: np.ndarray, filtro: str = "bilateral") -> ResultadoPreproc:
    """Executa a fase 2 completa sobre um quadro BGR."""
    cinza = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    luminosidade = medir_luminosidade(cinza)
    clahe_obj = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    clahe = clahe_obj.apply(cinza)
    filtrado = _filtrar(clahe, filtro)

    lab = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = _corrigir_gamma(clahe_obj.apply(l))
    normalizado = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2BGR)
    normalizado = _filtrar(normalizado, filtro)
    return ResultadoPreproc(cinza, clahe, filtrado, normalizado, luminosidade)


# ============================================================================
# MÓDULO: pipeline.segmentacao
# ============================================================================
from dataclasses import dataclass, field

import cv2
import numpy as np


Caixa = tuple[int, int, int, int]  # (topo, direita, base, esquerda)


@dataclass
class Rosto:
    """Rosto detectado: caixa delimitadora e pontos de referência (landmarks)."""

    caixa: Caixa
    pontos: dict[str, list[tuple[int, int]]] = field(default_factory=dict)

    @property
    def largura(self) -> int:
        return self.caixa[1] - self.caixa[3]

    @property
    def altura(self) -> int:
        return self.caixa[2] - self.caixa[0]

    @property
    def centro(self) -> tuple[float, float]:
        t, r, b, l = self.caixa
        return ((l + r) / 2.0, (t + b) / 2.0)


def detectar_rostos(rgb: np.ndarray, modelo: str = "hog", escala: float = 0.5) -> list[Rosto]:
    """Detecta rostos numa imagem RGB. A detecção roda em escala reduzida (rápido);
    as caixas e os 68 landmarks são calculados na resolução original."""
    if face_recognition is None:
        raise RuntimeError("face_recognition/dlib indisponível.")
    alt, larg = rgb.shape[:2]
    if 0 < escala < 1:
        pequena = cv2.resize(rgb, (max(1, int(larg * escala)), max(1, int(alt * escala))),
                             interpolation=cv2.INTER_AREA)
    else:
        pequena, escala = rgb, 1.0
    locais = face_recognition.face_locations(pequena, number_of_times_to_upsample=1 if modelo == "hog" else 0,
                                             model=modelo)
    if not locais:
        return []
    inv = 1.0 / escala
    caixas: list[Caixa] = [(max(0, int(t * inv)), min(larg - 1, int(r * inv)),
                            min(alt - 1, int(b * inv)), max(0, int(l * inv))) for t, r, b, l in locais]
    marcos = face_recognition.face_landmarks(rgb, caixas)
    return [Rosto(c, m) for c, m in zip(caixas, marcos)]


def recortar_rosto(frame: np.ndarray, caixa: Caixa, margem: float = 0.25) -> np.ndarray:
    """Recorta o rosto com uma margem proporcional."""
    t, r, b, l = caixa
    alt, larg = frame.shape[:2]
    mh, mw = int((b - t) * margem), int((r - l) * margem)
    return frame[max(0, t - mh):min(alt, b + mh), max(0, l - mw):min(larg, r + mw)].copy()


def desenhar_rostos(frame: np.ndarray, rostos: list[Rosto], cor_bgr: tuple[int, int, int] | None = None,
                    com_pontos: bool = True) -> None:
    """Desenha caixa delimitadora (cantos) e landmarks diretamente no quadro."""
    cor = cor_bgr or Cores.bgr(Cores.ROXO_BRILHO)
    for rosto in rostos:
        t, r, b, l = rosto.caixa
        cv2.rectangle(frame, (l, t), (r, b), cor, 2)
        c = max(12, (r - l) // 5)
        for (x, y, dx, dy) in ((l, t, 1, 1), (r, t, -1, 1), (l, b, 1, -1), (r, b, -1, -1)):
            cv2.line(frame, (x, y), (x + dx * c, y), cor, 4)
            cv2.line(frame, (x, y), (x, y + dy * c), cor, 4)
        if com_pontos:
            for lista in rosto.pontos.values():
                for (px, py) in lista:
                    cv2.circle(frame, (px, py), 2, (255, 255, 255), -1)


# ============================================================================
# MÓDULO: pipeline.extracao
# ============================================================================
from typing import Optional

import cv2
import numpy as np



def extrair_encoding(rgb: np.ndarray, caixa: Caixa, num_jitters: int = 1) -> Optional[np.ndarray]:
    """Calcula o vetor de 128 dimensões do rosto delimitado pela caixa. None se falhar."""
    if face_recognition is None:
        raise RuntimeError("face_recognition/dlib indisponível.")
    encs = face_recognition.face_encodings(rgb, known_face_locations=[caixa], num_jitters=num_jitters)
    return np.asarray(encs[0], dtype=np.float64) if encs else None


def visualizar_encoding(enc: Optional[np.ndarray], largura: int = 480, altura: int = 100) -> np.ndarray:
    """Desenha as 128 componentes do vetor como barras (imagem BGR)."""
    img = np.full((altura, largura, 3), Cores.bgr(Cores.PAINEL), dtype=np.uint8)
    meio = altura // 2
    cv2.line(img, (0, meio), (largura, meio), Cores.bgr(Cores.BORDA), 1)
    if enc is None:
        return img
    esp = largura / len(enc)
    vmax = max(0.3, float(np.abs(enc).max()))
    for i, v in enumerate(enc):
        x0, x1 = int(i * esp), max(int(i * esp) + 1, int((i + 1) * esp) - 1)
        h = int(abs(v) / vmax * (meio - 4))
        y0, y1 = (meio - h, meio) if v >= 0 else (meio, meio + h)
        cv2.rectangle(img, (x0, y0), (x1, y1), Cores.bgr(Cores.ROXO_BRILHO), -1)
    return img


# ============================================================================
# MÓDULO: pipeline.classificacao
# ============================================================================
import math
import threading
from dataclasses import dataclass
from typing import Optional

import numpy as np



@dataclass
class ResultadoClassificacao:
    """Resultado da comparação do rosto com a base de cadastrados."""

    identificado: bool
    usuario_id: Optional[int]
    nome: str
    cargo: str
    nivel: int
    divisao: str
    distancia: float
    confianca: float  # 0–100
    total_cadastrados: int


def distancia_para_confianca(distancia: float, tolerancia: float) -> float:
    """Converte distância em % de confiança (50% exatamente na tolerância; 100% na distância 0)."""
    faixa = 1.0 - tolerancia
    linear = (1.0 - distancia) / (faixa * 2.0)
    if distancia > tolerancia:
        valor = linear
    else:
        valor = linear + ((1.0 - linear) * math.pow((linear - 0.5) * 2.0, 0.2))
    return float(min(1.0, max(0.0, valor)) * 100.0)


class Classificador:
    """Mantém em memória os encodings decifrados e compara por distância euclidiana."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._matriz = np.empty((0, 128))
        self._meta: list[Usuario] = []

    def recarregar(self, usuarios: list[Usuario]) -> None:
        """Substitui a base (usuários precisam vir com encoding)."""
        validos = [u for u in usuarios if u.encoding is not None]
        matriz = np.vstack([u.encoding for u in validos]) if validos else np.empty((0, 128))
        with self._lock:
            self._matriz, self._meta = matriz, validos

    def classificar(self, encoding: np.ndarray, tolerancia: float) -> ResultadoClassificacao:
        """Compara com todos os cadastrados e devolve o mais próximo."""
        with self._lock:
            matriz, meta = self._matriz, self._meta
        if len(meta) == 0:
            return ResultadoClassificacao(False, None, "desconhecido", "", 0, "", 1.0, 0.0, 0)
        distancias = np.linalg.norm(matriz - encoding, axis=1)
        i = int(np.argmin(distancias))
        d = float(distancias[i])
        conf = distancia_para_confianca(d, tolerancia)
        if d <= tolerancia:
            u = meta[i]
            return ResultadoClassificacao(True, u.id, u.nome, u.cargo, u.nivel, u.divisao, d, conf, len(meta))
        return ResultadoClassificacao(False, None, "desconhecido", "", 0, "", d, conf, len(meta))

    def encontrar_duplicado(self, encoding: np.ndarray, tolerancia: float) -> Optional[ResultadoClassificacao]:
        """Se o rosto já pertence a alguém cadastrado, devolve essa pessoa."""
        r = self.classificar(encoding, tolerancia)
        return r if r.identificado else None


# ============================================================================
# MÓDULO: pipeline.vivacidade
# ============================================================================
from collections import deque
from typing import Optional

import numpy as np



def _ear_olho(pontos: list[tuple[int, int]]) -> Optional[float]:
    """EAR = (|p2-p6| + |p3-p5|) / (2·|p1-p4|) com os 6 landmarks do olho."""
    if len(pontos) < 6:
        return None
    p = np.asarray(pontos[:6], dtype=float)
    horizontal = np.linalg.norm(p[0] - p[3])
    if horizontal <= 0:
        return None
    return float((np.linalg.norm(p[1] - p[5]) + np.linalg.norm(p[2] - p[4])) / (2.0 * horizontal))


def calcular_ear(marcos: dict[str, list[tuple[int, int]]]) -> Optional[float]:
    """EAR médio dos dois olhos."""
    valores = [v for v in (_ear_olho(marcos.get("left_eye", [])), _ear_olho(marcos.get("right_eye", [])))
               if v is not None]
    return float(np.mean(valores)) if valores else None


class DetectorPiscada:
    """Conta piscadas: olho 'fechado' = EAR bem abaixo do EAR aberto recente (adaptativo)."""

    def __init__(self) -> None:
        self._hist: deque[float] = deque(maxlen=40)
        self._fechado = False
        self.piscadas = 0
        self.ultimo_ear = 0.0

    def resetar(self) -> None:
        self._hist.clear()
        self._fechado = False
        self.piscadas = 0

    @property
    def piscou(self) -> bool:
        return self.piscadas >= 1

    def atualizar(self, ear: float) -> None:
        """Atualiza com o EAR do quadro atual."""
        self.ultimo_ear = ear
        self._hist.append(ear)
        if len(self._hist) < 6:
            return
        aberto = float(np.percentile(self._hist, 80))
        fechado = ear < aberto * EAR_RAZAO or ear < EAR_FECHADO_ABS
        if aberto < 0.12:  # landmarks instáveis
            return
        if fechado:
            self._fechado = True
        elif self._fechado:
            self._fechado = False
            self.piscadas += 1


# ============================================================================
# MÓDULO: pipeline.tipos
# ============================================================================
from dataclasses import dataclass, field
from typing import Optional

import numpy as np


NOMES_FASES = {
    1: "1 · Aquisição",
    2: "2 · Pré-processamento",
    3: "3 · Segmentação",
    4: "4 · Extração (128-D)",
    5: "5 · Classificação",
}


def _status_inicial() -> dict[int, str]:
    return {i: "inativo" for i in range(1, 6)}


@dataclass
class ResultadoPipeline:
    """Saída completa do processamento de um quadro (fases 1 a 5).

    status por fase: "ok", "aviso", "erro" ou "inativo".
    erro: "" ou código (sem_camera, modelos, pouca_luz, excesso_luz, sem_rosto,
    multiplos_rostos, borrada, falha_encoding, falha_pipeline).
    """

    timestamp: float
    original: Optional[np.ndarray] = None
    pre: Optional[ResultadoPreproc] = None
    rostos: list[Rosto] = field(default_factory=list)
    encoding: Optional[np.ndarray] = None
    classificacao: Optional[ResultadoClassificacao] = None
    erro: str = ""
    mensagem: str = ""
    ear: Optional[float] = None
    crop: Optional[np.ndarray] = None
    luminosidade: float = 0.0
    nitidez: float = 0.0
    tempos_ms: dict[int, float] = field(default_factory=dict)
    status: dict[int, str] = field(default_factory=_status_inicial)
    detalhes: dict[int, str] = field(default_factory=dict)

    @property
    def n_rostos(self) -> int:
        return len(self.rostos)


# ============================================================================
# MÓDULO: pipeline.decisao
# ============================================================================
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import cv2
import numpy as np


log = logging.getLogger(__name__)


@dataclass
class Decisao:
    """Decisão final: ACESSO CONCEDIDO ou NEGADO."""

    concedido: bool
    usuario_id: Optional[int]
    nome: str
    cargo: str
    nivel_usuario: int
    divisao: str
    nivel_solicitado: int
    confianca: float
    distancia: float
    motivo: str
    vivacidade_ok: bool
    captura_jpg: Optional[bytes]
    bloqueou: bool = False
    timestamp: float = field(default_factory=time.time)


class ControleBloqueio:
    """3 tentativas negadas seguidas = bloqueio de 60 s (persistido: reiniciar o programa não burla)."""

    def __init__(self, banco: Banco) -> None:
        self.banco = banco

    @property
    def tentativas(self) -> int:
        try:
            return int(self.banco.obter_config("tentativas_negadas", "0"))
        except ValueError:
            return 0

    def segundos_restantes(self) -> int:
        try:
            ate = float(self.banco.obter_config("bloqueado_ate", "0"))
        except ValueError:
            ate = 0.0
        restante = ate - time.time()
        if restante <= 0:
            if ate > 0:  # bloqueio expirou: zera
                self.banco.definir_config("bloqueado_ate", "0")
                self.banco.definir_config("tentativas_negadas", "0")
            return 0
        return int(restante) + 1

    def registrar(self, concedido: bool) -> bool:
        """Registra resultado. Retorna True se este resultado ACIONOU o bloqueio."""
        if concedido:
            self.banco.definir_config("tentativas_negadas", "0")
            return False
        n = self.tentativas + 1
        if n >= MAX_TENTATIVAS:
            self.banco.definir_config("bloqueado_ate", time.time() + BLOQUEIO_S)
            self.banco.definir_config("tentativas_negadas", "0")
            return True
        self.banco.definir_config("tentativas_negadas", n)
        return False


class Decisor:
    """Máquina de estados da autenticação contínua.

    aguardando → estabilizando (≈2 s) → aguardando_piscada → resultado (→ aguardando)
    """

    def __init__(self, classificador: Classificador, obter_ajustes: Callable[[], Ajustes],
                 bloqueio: ControleBloqueio) -> None:
        self.classificador = classificador
        self.obter_ajustes = obter_ajustes
        self.bloqueio = bloqueio
        self._piscada = DetectorPiscada()
        self.estado = "aguardando"
        self.mensagem = "Posicione o rosto em frente à câmera."
        self.progresso = 0.0
        self.ultima_decisao: Optional[Decisao] = None
        self._resetar_janela()
        self._t_sem_rosto: Optional[float] = None
        self._t_resultado = 0.0
        self._ultimo_ok = 0.0

    @property
    def piscadas(self) -> int:
        return self._piscada.piscadas

    def _resetar_janela(self) -> None:
        self._ref: Optional[tuple[float, float, int]] = None
        self._t_inicio: Optional[float] = None
        self._encs: list[np.ndarray] = []
        self._crop: Optional[np.ndarray] = None
        self._piscada.resetar()
        self.progresso = 0.0

    def reiniciar(self) -> None:
        """Volta ao estado inicial (ao entrar na aba ou após um resultado)."""
        self._resetar_janela()
        self._t_sem_rosto = None
        self.estado = "aguardando"
        self.mensagem = "Posicione o rosto em frente à câmera."

    def _estavel(self, cx: float, cy: float, w: int) -> bool:
        if self._ref is None:
            return False
        rcx, rcy, rw = self._ref
        return abs(cx - rcx) <= 0.30 * rw and abs(cy - rcy) <= 0.30 * rw and 0.7 <= w / max(rw, 1) <= 1.4

    def atualizar(self, res: ResultadoPipeline, nivel_solicitado: int) -> Optional[Decisao]:
        """Processa um resultado do pipeline; devolve Decisao quando houver decisão final."""
        agora = res.timestamp
        ajustes = self.obter_ajustes()

        restante = self.bloqueio.segundos_restantes()
        if restante > 0:
            self.estado = "bloqueado"
            self.mensagem = f"SISTEMA BLOQUEADO por tentativas negadas. Aguarde {restante} s."
            self._resetar_janela()
            return None
        if self.estado == "bloqueado":
            self.reiniciar()

        if self.estado == "resultado":  # espera a pessoa sair de frente da câmera
            if res.n_rostos == 0:
                self._t_sem_rosto = self._t_sem_rosto or agora
                if agora - self._t_sem_rosto > 1.2:
                    self.reiniciar()
            else:
                self._t_sem_rosto = None
                if agora - self._t_resultado > 8.0:
                    self.reiniciar()
            return None

        if res.erro:
            if res.erro == "sem_rosto" and self._ref is not None and agora - self._ultimo_ok < 0.6:
                return None  # falha momentânea de detecção: mantém a janela
            self.mensagem = res.mensagem
            self.estado = "aguardando"
            self._resetar_janela()
            return None

        rosto = res.rostos[0]
        cx, cy = rosto.centro
        self._ultimo_ok = agora
        if not self._estavel(cx, cy, rosto.largura):
            self._resetar_janela()
            self._ref, self._t_inicio = (cx, cy, rosto.largura), agora
        if res.ear is not None:
            self._piscada.atualizar(res.ear)
        if res.encoding is not None:
            self._encs.append(res.encoding)
            self._crop = res.crop

        decorrido = agora - (self._t_inicio or agora)
        self.progresso = min(1.0, decorrido / ajustes.tempo_estavel)
        if decorrido < ajustes.tempo_estavel or len(self._encs) < 2:
            self.estado = "estabilizando"
            self.mensagem = "Mantenha o rosto estável e olhe para a câmera..."
            return None
        if not self._piscada.piscou:
            self.estado = "aguardando_piscada"
            self.mensagem = "Pisque os olhos para confirmar vivacidade."
            if decorrido > ajustes.tempo_estavel + LIMITE_VIVACIDADE_S:
                return self._decidir(agora, nivel_solicitado, ajustes, vivo=False)
            return None
        return self._decidir(agora, nivel_solicitado, ajustes, vivo=True)

    def _decidir(self, agora: float, nivel_solicitado: int, ajustes: Ajustes, vivo: bool) -> Decisao:
        enc = np.mean(np.vstack(self._encs), axis=0)
        c = self.classificador.classificar(enc, ajustes.tolerancia)
        if not vivo:
            motivo, ok = "Vivacidade não comprovada (nenhuma piscada detectada): possível foto ou vídeo.", False
        elif not c.identificado:
            motivo, ok = (f"Rosto não reconhecido (distância {c.distancia:.3f} > tolerância "
                          f"{ajustes.tolerancia:.2f}).", False)
        elif c.nivel < nivel_solicitado:
            motivo, ok = f"Nível insuficiente: usuário nível {c.nivel}, acesso solicitado ao nível {nivel_solicitado}.", False
        else:
            motivo, ok = f"Identidade confirmada; nível {c.nivel} atende ao nível {nivel_solicitado}.", True
        jpg: Optional[bytes] = None
        if self._crop is not None:
            enc_ok, buf = cv2.imencode(".jpg", self._crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
            jpg = buf.tobytes() if enc_ok else None
        bloqueou = False
        try:
            bloqueou = self.bloqueio.registrar(ok)
        except Exception:  # noqa: BLE001
            log.exception("Falha ao registrar tentativa")
        d = Decisao(ok, c.usuario_id, c.nome if c.identificado else "desconhecido", c.cargo, c.nivel, c.divisao,
                    nivel_solicitado, c.confianca, c.distancia, motivo, vivo, jpg, bloqueou)
        self.ultima_decisao = d
        self.estado, self._t_resultado, self._t_sem_rosto = "resultado", agora, None
        self.mensagem = "ACESSO CONCEDIDO" if ok else "ACESSO NEGADO"
        self._resetar_janela()
        return d


# ============================================================================
# MÓDULO: pipeline.orquestrador
# ============================================================================
import logging
import queue
import threading
import time
from typing import Callable, Optional

import cv2
import numpy as np


log = logging.getLogger(__name__)
_TRAVA_DLIB = threading.Lock()  # dlib não é garantidamente thread-safe


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


def processar_quadro(frame: Optional[np.ndarray], ajustes: Ajustes, classificador: Optional[Classificador] = None,
                     *, codificar: bool = True, num_jitters: int = 1) -> ResultadoPipeline:
    """Executa as fases 1–5 em um quadro BGR e devolve tudo (imagens, status, tempos, mensagens)."""
    res = ResultadoPipeline(timestamp=time.time(), original=frame)
    if frame is None:
        res.erro, res.mensagem, res.status[1] = "sem_camera", "Sem imagem da câmera.", "erro"
        return res
    res.status[1] = "ok"
    res.detalhes[1] = f"{frame.shape[1]}x{frame.shape[0]}"
    try:
        with _TRAVA_DLIB:
            _executar(res, frame, ajustes, classificador, codificar, num_jitters)
    except Exception as exc:  # noqa: BLE001
        log.exception("Falha no pipeline")
        res.erro, res.mensagem = "falha_pipeline", f"Falha no processamento da imagem: {exc}"
        for f in (2, 3, 4, 5):
            if res.status[f] == "inativo":
                res.status[f] = "erro"
                break
    return res


def _executar(res: ResultadoPipeline, frame: np.ndarray, aj: Ajustes, clf: Optional[Classificador],
              codificar: bool, jitters: int) -> None:
    # FASE 2 — pré-processamento
    t0 = time.perf_counter()
    pre = preprocessar(frame, aj.filtro)
    res.pre, res.luminosidade, res.tempos_ms[2] = pre, pre.luminosidade, _ms(t0)
    res.detalhes[2] = f"luz {pre.luminosidade:.0f} · {aj.filtro}"
    if pre.luminosidade < aj.luz_min:
        res.erro, res.status[2] = "pouca_luz", "aviso"
        res.mensagem = f"Pouca luz ({pre.luminosidade:.0f}). Aumente a iluminação do ambiente."
        return
    if pre.luminosidade > aj.luz_max:
        res.erro, res.status[2] = "excesso_luz", "aviso"
        res.mensagem = f"Excesso de luz ({pre.luminosidade:.0f}). Evite luz direta ou contraluz."
        return
    res.status[2] = "ok"

    # FASE 3 — segmentação
    t0 = time.perf_counter()
    rgb = cv2.cvtColor(pre.normalizado, cv2.COLOR_BGR2RGB)
    rostos = detectar_rostos(rgb, aj.modelo_deteccao, 0.5 if aj.modelo_deteccao == "hog" else 0.75)
    res.rostos, res.tempos_ms[3] = rostos, _ms(t0)
    res.detalhes[3] = f"{aj.modelo_deteccao.upper()} · {len(rostos)} rosto(s)"
    if not rostos:
        res.erro, res.status[3] = "sem_rosto", "aviso"
        res.mensagem = "Nenhum rosto detectado. Posicione-se de frente para a câmera."
        return
    if len(rostos) > 1:
        res.erro, res.status[3] = "multiplos_rostos", "aviso"
        res.mensagem = f"{len(rostos)} rostos detectados. Apenas uma pessoa por vez."
        return
    rosto = rostos[0]
    res.ear = calcular_ear(rosto.pontos)
    res.crop = recortar_rosto(frame, rosto.caixa)
    res.nitidez = medir_nitidez(res.crop)
    if res.nitidez < aj.nitidez_min:
        res.erro, res.status[3] = "borrada", "aviso"
        res.mensagem = f"Imagem borrada (nitidez {res.nitidez:.0f}). Fique parado e limpe a lente."
        return
    res.status[3] = "ok"

    # FASE 4 — extração de características
    if not codificar:
        return
    t0 = time.perf_counter()
    enc = extrair_encoding(rgb, rosto.caixa, jitters)
    res.tempos_ms[4] = _ms(t0)
    if enc is None:
        res.erro, res.status[4] = "falha_encoding", "erro"
        res.mensagem = "Não foi possível extrair as características do rosto."
        return
    res.encoding, res.status[4], res.detalhes[4] = enc, "ok", "vetor de 128 dimensões"

    # FASE 5 — classificação
    if clf is not None:
        t0 = time.perf_counter()
        c = clf.classificar(enc, aj.tolerancia)
        res.classificacao, res.tempos_ms[5] = c, _ms(t0)
        res.status[5] = "ok" if c.identificado else "aviso"
        res.detalhes[5] = f"{c.nome} · {c.confianca:.0f}%" if c.identificado else \
            f"desconhecido · d={c.distancia:.2f}"


class LoopReconhecimento(threading.Thread):
    """Processa quadros continuamente fora da thread da GUI.

    modos: "idle" (parado), "auth" (autenticação com decisão), "cadastro" e "pipeline" (só análise).
    """

    INTERVALO_ENCODING = {"auth": 0.3, "cadastro": 0.0, "pipeline": 0.0}

    def __init__(self, camera: Camera, classificador: Classificador, obter_ajustes: Callable[[], Ajustes],
                 decisor: Decisor) -> None:
        super().__init__(daemon=True, name="LoopReconhecimento")
        self.camera, self.classificador, self.obter_ajustes, self.decisor = camera, classificador, obter_ajustes, decisor
        self.modo = "idle"
        self.nivel_solicitado = 1
        self.modelos_ok, self.msg_modelos = True, ""
        self.ultimo: Optional[ResultadoPipeline] = None
        self.fila: "queue.Queue[Decisao]" = queue.Queue()
        self._parar = threading.Event()
        self._ultimo_id = -1
        self._t_enc = 0.0

    def definir_modo(self, modo: str) -> None:
        self.modo = modo
        self._t_enc = 0.0

    def parar(self) -> None:
        self._parar.set()

    def run(self) -> None:
        while not self._parar.is_set():
            if self.modo == "idle":
                time.sleep(0.1)
                continue
            frame, fid = self.camera.ler_com_id()
            if frame is None:
                self.ultimo = ResultadoPipeline(time.time(), erro="sem_camera", mensagem=self.camera.erro or "Sem imagem da câmera.")
                self.ultimo.status[1] = "erro"
                time.sleep(0.1)
                continue
            if fid == self._ultimo_id:
                time.sleep(0.01)
                continue
            self._ultimo_id = fid
            modo = self.modo
            try:
                self._processar(frame, modo)
            except Exception:  # noqa: BLE001
                log.exception("Erro no laço de reconhecimento")
                time.sleep(0.2)

    def _processar(self, frame: np.ndarray, modo: str) -> None:
        if not self.modelos_ok:
            r = ResultadoPipeline(time.time(), original=frame, erro="modelos", mensagem=self.msg_modelos)
            r.status[1], r.status[2] = "ok", "erro"
            self.ultimo = r
            time.sleep(0.2)
            return
        agora = time.time()
        intervalo = self.INTERVALO_ENCODING.get(modo, 0.0)
        codificar = (agora - self._t_enc) >= intervalo
        anterior = self.ultimo
        res = processar_quadro(frame, self.obter_ajustes(), self.classificador, codificar=codificar)
        if codificar and res.encoding is not None:
            self._t_enc = agora
        elif not codificar and not res.erro and anterior is not None:  # mantém exibição das fases 4–5
            for f in (4, 5):
                res.status[f] = anterior.status[f]
                res.detalhes[f] = anterior.detalhes.get(f, "")
                if f in anterior.tempos_ms:
                    res.tempos_ms[f] = anterior.tempos_ms[f]
            res.classificacao = anterior.classificacao
        res.tempos_ms[1] = 1000.0 / self.camera.fps if self.camera.fps > 0 else 0.0
        self.ultimo = res
        if modo == "auth":
            res.status[5] = res.status[5]
            decisao = self.decisor.atualizar(res, self.nivel_solicitado)
            if decisao is not None:
                self.fila.put(decisao)


# ============================================================================
# MÓDULO: reports.estatisticas
# ============================================================================
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional


PERIODOS = {"Hoje": 0, "Últimos 7 dias": 7, "Últimos 30 dias": 30, "Todo o período": None}
_FILTRO_ACESSO = "resultado IN ('CONCEDIDO','NEGADO')"


@dataclass
class Estatisticas:
    """Indicadores do período."""

    periodo: str
    total: int = 0
    concedidos: int = 0
    negados: int = 0
    por_nivel: dict[int, tuple[int, int]] = field(default_factory=dict)   # nível -> (concedidos, negados)
    por_dia: list[tuple[str, int, int]] = field(default_factory=list)     # dia -> (concedidos, negados)
    top_usuarios: list[tuple[str, int]] = field(default_factory=list)

    @property
    def taxa_sucesso(self) -> float:
        return 100.0 * self.concedidos / self.total if self.total else 0.0

    @property
    def taxa_negacao(self) -> float:
        return 100.0 * self.negados / self.total if self.total else 0.0


def inicio_periodo(periodo: str) -> Optional[str]:
    """Data/hora inicial do período escolhido (None = sem limite)."""
    dias = PERIODOS.get(periodo)
    if dias is None:
        return None
    base = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return (base - timedelta(days=dias)).strftime("%Y-%m-%d %H:%M:%S")


def coletar(banco: Banco, periodo: str) -> Estatisticas:
    """Calcula totais, taxas, tentativas por nível, acessos por dia e usuários mais ativos."""
    desde = inicio_periodo(periodo)
    filtro = f"{_FILTRO_ACESSO} AND data_hora >= ?" if desde else _FILTRO_ACESSO
    params: tuple = (desde,) if desde else ()
    est = Estatisticas(periodo)
    soma = "SUM(resultado='CONCEDIDO') AS c, SUM(resultado='NEGADO') AS n"
    linha = banco.executar_consulta(f"SELECT COUNT(*) AS t, {soma} FROM logs WHERE {filtro}", params)[0]
    est.total, est.concedidos, est.negados = int(linha["t"] or 0), int(linha["c"] or 0), int(linha["n"] or 0)
    for r in banco.executar_consulta(
            f"SELECT nivel_solicitado AS nv, {soma} FROM logs WHERE {filtro} AND nivel_solicitado IS NOT NULL "
            "GROUP BY nivel_solicitado ORDER BY nv", params):
        est.por_nivel[int(r["nv"])] = (int(r["c"] or 0), int(r["n"] or 0))
    for r in banco.executar_consulta(
            f"SELECT substr(data_hora,1,10) AS d, {soma} FROM logs WHERE {filtro} GROUP BY d ORDER BY d", params):
        est.por_dia.append((r["d"], int(r["c"] or 0), int(r["n"] or 0)))
    for r in banco.executar_consulta(
            f"SELECT usuario_nome AS u, COUNT(*) AS q FROM logs WHERE {filtro} AND usuario_id IS NOT NULL "
            "GROUP BY usuario_nome ORDER BY q DESC, u LIMIT 8", params):
        est.top_usuarios.append((r["u"], int(r["q"])))
    return est


# ============================================================================
# MÓDULO: reports.graficos
# ============================================================================
from matplotlib.figure import Figure



def _paleta(escuro: bool) -> dict[str, str]:
    if escuro:
        return dict(fundo=Cores.PAINEL, texto=Cores.TEXTO, sec=Cores.TEXTO_SEC, grade=Cores.BORDA,
                    roxo=Cores.ROXO_BRILHO, roxo2=Cores.ROXO_ESCURO)
    return dict(fundo="#FFFFFF", texto="#1F1E1D", sec="#555555", grade="#DDDDDD",
                roxo=Cores.ROXO, roxo2="#C4B5FD")


def _figura(titulo: str, escuro: bool, tamanho: tuple[float, float] = (5.2, 3.0)):
    p = _paleta(escuro)
    fig = Figure(figsize=tamanho, dpi=100, facecolor=p["fundo"], layout="constrained")
    ax = fig.add_subplot(111)
    ax.set_facecolor(p["fundo"])
    ax.set_title(titulo, color=p["texto"], fontsize=11, fontweight="bold", loc="left")
    ax.tick_params(colors=p["sec"], labelsize=8)
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    for lado in ("left", "bottom"):
        ax.spines[lado].set_color(p["grade"])
    ax.grid(axis="y", color=p["grade"], linewidth=0.6, alpha=0.7)
    ax.set_axisbelow(True)
    return fig, ax, p


def _vazio(ax, p) -> None:
    ax.text(0.5, 0.5, "Sem dados no período", ha="center", va="center", color=p["sec"], transform=ax.transAxes)
    ax.set_xticks([])
    ax.set_yticks([])


def grafico_por_dia(est: Estatisticas, escuro: bool = True) -> Figure:
    """Acessos (concedidos + negados) por dia."""
    fig, ax, p = _figura("Acessos por período", escuro)
    if not est.por_dia:
        _vazio(ax, p)
        return fig
    dias = [d[5:] if len(d) == 10 else d for d, _, _ in est.por_dia]
    totais = [c + n for _, c, n in est.por_dia]
    ax.plot(range(len(dias)), totais, color=p["roxo"], linewidth=2.2, marker="o", markersize=5)
    ax.fill_between(range(len(dias)), totais, color=p["roxo"], alpha=0.18)
    passo = max(1, len(dias) // 8)
    ax.set_xticks(range(0, len(dias), passo))
    ax.set_xticklabels(dias[::passo])
    ax.set_ylim(bottom=0)
    ax.yaxis.get_major_locator().set_params(integer=True)
    return fig


def grafico_por_nivel(est: Estatisticas, escuro: bool = True) -> Figure:
    """Tentativas por nível solicitado (concedidas × negadas)."""
    fig, ax, p = _figura("Tentativas por nível", escuro)
    if not est.por_nivel:
        _vazio(ax, p)
        return fig
    niveis = sorted(est.por_nivel)
    x = range(len(niveis))
    ax.bar([i - 0.2 for i in x], [est.por_nivel[n][0] for n in niveis], width=0.4, color=p["roxo"], label="Concedidos")
    ax.bar([i + 0.2 for i in x], [est.por_nivel[n][1] for n in niveis], width=0.4, color=p["roxo2"], label="Negados")
    ax.set_xticks(list(x))
    ax.set_xticklabels([f"Nível {n}" for n in niveis])
    ax.yaxis.get_major_locator().set_params(integer=True)
    leg = ax.legend(fontsize=8, frameon=False)
    for t in leg.get_texts():
        t.set_color(p["texto"])
    return fig


def grafico_top_usuarios(est: Estatisticas, escuro: bool = True) -> Figure:
    """Usuários mais ativos (nº de tentativas)."""
    fig, ax, p = _figura("Usuários mais ativos", escuro)
    if not est.top_usuarios:
        _vazio(ax, p)
        return fig
    dados = list(reversed(est.top_usuarios))
    ax.grid(axis="y", visible=False)
    ax.grid(axis="x", color=p["grade"], linewidth=0.6, alpha=0.7)
    ax.barh([n if len(n) <= 22 else n[:21] + "…" for n, _ in dados], [q for _, q in dados], color=p["roxo"])
    ax.xaxis.get_major_locator().set_params(integer=True)
    return fig


def grafico_resultado(est: Estatisticas, escuro: bool = True) -> Figure:
    """Proporção concedido × negado (as únicas cores semânticas do sistema)."""
    fig, ax, p = _figura("Taxa de sucesso / negação", escuro)
    ax.grid(False)
    if est.total == 0:
        _vazio(ax, p)
        return fig
    ax.pie([est.concedidos, est.negados], colors=[Cores.VERDE, Cores.VERMELHO],
           labels=[f"Concedido\n{est.taxa_sucesso:.0f}%", f"Negado\n{est.taxa_negacao:.0f}%"],
           textprops=dict(color=p["texto"], fontsize=9), startangle=90,
           wedgeprops=dict(width=0.38, edgecolor=p["fundo"]))
    return fig


# ============================================================================
# MÓDULO: reports.exportar
# ============================================================================
import csv
import io
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


CAB_CSV = ["data_hora", "usuario", "resultado", "nivel_solicitado", "confianca_%", "motivo"]


def _logs_periodo(banco: Banco, periodo: str, limite: int = 100000):
    desde = inicio_periodo(periodo)
    sql = ("SELECT data_hora, usuario_nome, resultado, nivel_solicitado, confianca, motivo FROM logs "
           + ("WHERE data_hora >= ? " if desde else "") + "ORDER BY id DESC LIMIT ?")
    return banco.executar_consulta(sql, ((desde,) if desde else ()) + (limite,))


def exportar_csv(banco: Banco, periodo: str, destino: Path) -> int:
    """Exporta o log de auditoria do período (separador ';', UTF-8 com BOM). Retorna nº de linhas."""
    linhas = _logs_periodo(banco, periodo)
    with open(destino, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(CAB_CSV)
        for r in linhas:
            conf = "" if r["confianca"] is None else f"{r['confianca']:.1f}".replace(".", ",")
            w.writerow([r["data_hora"], r["usuario_nome"], r["resultado"], r["nivel_solicitado"] or "", conf, r["motivo"]])
    return len(linhas)


def _png(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, facecolor=fig.get_facecolor())
    buf.seek(0)
    return buf


def exportar_pdf(banco: Banco, est: Estatisticas, destino: Path) -> None:
    """Gera o relatório em PDF com indicadores, gráficos e os últimos eventos."""
    estilos = getSampleStyleSheet()
    titulo = ParagraphStyle("t", parent=estilos["Title"], textColor=colors.HexColor("#6D28D9"), fontSize=20)
    h2 = ParagraphStyle("h2", parent=estilos["Heading2"], textColor=colors.HexColor("#1F1E1D"))
    pequeno = ParagraphStyle("p", parent=estilos["BodyText"], fontSize=7.5, leading=9)
    doc = SimpleDocTemplate(str(destino), pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm,
                            topMargin=1.5 * cm, bottomMargin=1.5 * cm, title=f"{APP_NOME} — Relatório")
    roxo = colors.HexColor("#6D28D9")
    el: list = [Paragraph(f"{APP_NOME} — Relatório de Acessos", titulo),
                Paragraph(f"Período: <b>{est.periodo}</b> &nbsp;·&nbsp; Gerado em {datetime.now():%d/%m/%Y %H:%M}",
                          estilos["BodyText"]), Spacer(1, 10)]

    kpi = Table([["Total de acessos", "Concedidos", "Negados", "Taxa de sucesso", "Taxa de negação"],
                 [est.total, est.concedidos, est.negados, f"{est.taxa_sucesso:.1f}%", f"{est.taxa_negacao:.1f}%"]],
                colWidths=[3.5 * cm] * 5)
    kpi.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), roxo), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                             ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("FONTSIZE", (0, 0), (-1, 0), 8),
                             ("FONTSIZE", (0, 1), (-1, 1), 13), ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
                             ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    el += [kpi, Spacer(1, 12), Paragraph("Tentativas por nível", h2)]
    dados_nivel = [["Nível solicitado", "Concedidos", "Negados", "Total"]] + [
        [NIVEIS_NOMES[n].split(" — ")[0], c, ng, c + ng] for n, (c, ng) in sorted(est.por_nivel.items())]
    if len(dados_nivel) == 1:
        dados_nivel.append(["—", "—", "—", "—"])
    t2 = Table(dados_nivel, colWidths=[5 * cm, 3.5 * cm, 3.5 * cm, 3.5 * cm])
    t2.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDE9FE")),
                            ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey), ("ALIGN", (1, 0), (-1, -1), "CENTER")]))
    el += [t2, Spacer(1, 12), Paragraph("Gráficos", h2)]

    imgs = [Image(_png(f(est, False)), width=8.6 * cm, height=5.0 * cm) for f in (
        grafico_por_dia, grafico_por_nivel, grafico_top_usuarios, grafico_resultado)]
    grade = Table([[imgs[0], imgs[1]], [imgs[2], imgs[3]]], colWidths=[9 * cm, 9 * cm])
    el += [grade, Spacer(1, 10), Paragraph("Últimos eventos do período", h2)]

    linhas = _logs_periodo(banco, est.periodo, 30)
    tab = [["Data/hora", "Usuário", "Resultado", "Nív.", "Conf.", "Motivo"]]
    for r in linhas:
        conf = "—" if r["confianca"] is None else f"{r['confianca']:.0f}%"
        tab.append([Paragraph(r["data_hora"], pequeno), Paragraph(r["usuario_nome"], pequeno),
                    Paragraph(r["resultado"], pequeno), r["nivel_solicitado"] or "—", conf,
                    Paragraph(r["motivo"][:140], pequeno)])
    t3 = Table(tab, colWidths=[2.6 * cm, 3 * cm, 2.2 * cm, 1 * cm, 1.2 * cm, 7.8 * cm], repeatRows=1)
    t3.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), roxo), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                            ("FONTSIZE", (0, 0), (-1, -1), 7.5), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("GRID", (0, 0), (-1, -1), 0.3, colors.lightgrey),
                            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F3FF")])]))
    el.append(t3)
    doc.build(el)


# ============================================================================
# MÓDULO: gui.tema
# ============================================================================
from tkinter import ttk
from typing import Any

import customtkinter as ctk


FAMILIA = "Segoe UI"


def aplicar_tema(raiz: ctk.CTk) -> None:
    """Configura aparência global e estilo dos Treeviews."""
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    raiz.configure(fg_color=Cores.FUNDO)
    e = ttk.Style(raiz)
    e.theme_use("clam")
    e.configure("Treeview", background=Cores.PAINEL, fieldbackground=Cores.PAINEL, foreground=Cores.TEXTO,
                rowheight=28, borderwidth=0, font=(FAMILIA, 10))
    e.configure("Treeview.Heading", background=Cores.BORDA, foreground=Cores.TEXTO, relief="flat",
                font=(FAMILIA, 10, "bold"))
    e.map("Treeview", background=[("selected", Cores.ROXO)], foreground=[("selected", "#FFFFFF")])
    e.map("Treeview.Heading", background=[("active", Cores.BORDA)])
    e.configure("Vertical.TScrollbar", background=Cores.BORDA, troughcolor=Cores.PAINEL,
                bordercolor=Cores.PAINEL, arrowcolor=Cores.TEXTO, lightcolor=Cores.BORDA, darkcolor=Cores.BORDA)


def fonte(tam: int = 13, negrito: bool = False) -> ctk.CTkFont:
    return ctk.CTkFont(family=FAMILIA, size=tam, weight="bold" if negrito else "normal")


def _mesclar(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    base.update(extra)
    return base


def cartao(master: Any, **kw: Any) -> ctk.CTkFrame:
    """Card arredondado com borda."""
    return ctk.CTkFrame(master, **_mesclar(dict(fg_color=Cores.PAINEL, corner_radius=14, border_width=1,
                                                border_color=Cores.BORDA), kw))


def rotulo(master: Any, texto: str = "", secundario: bool = False, tam: int = 13, negrito: bool = False,
           **kw: Any) -> ctk.CTkLabel:
    return ctk.CTkLabel(master, text=texto, **_mesclar(dict(
        text_color=Cores.TEXTO_SEC if secundario else Cores.TEXTO, font=fonte(tam, negrito),
        anchor="w", justify="left"), kw))


def botao(master: Any, texto: str, comando: Any, primario: bool = True, **kw: Any) -> ctk.CTkButton:
    cores = dict(fg_color=Cores.ROXO, hover_color=Cores.ROXO_HOVER, text_color="#FFFFFF") if primario else \
        dict(fg_color=Cores.BORDA, hover_color="#4A4843", text_color=Cores.TEXTO)
    return ctk.CTkButton(master, text=texto, command=comando, **_mesclar(dict(
        corner_radius=10, height=36, font=fonte(13, True), text_color_disabled=Cores.TEXTO_SEC, **cores), kw))


def campo(master: Any, placeholder: str = "", **kw: Any) -> ctk.CTkEntry:
    return ctk.CTkEntry(master, placeholder_text=placeholder, **_mesclar(dict(
        fg_color=Cores.FUNDO, border_color=Cores.BORDA, text_color=Cores.TEXTO, height=36, corner_radius=10,
        font=fonte(13)), kw))


def segmentado(master: Any, valores: list[str], comando: Any = None, **kw: Any) -> ctk.CTkSegmentedButton:
    return ctk.CTkSegmentedButton(master, values=valores, command=comando, **_mesclar(dict(
        fg_color=Cores.FUNDO, selected_color=Cores.ROXO, selected_hover_color=Cores.ROXO_HOVER,
        unselected_color=Cores.FUNDO, unselected_hover_color=Cores.BORDA, text_color=Cores.TEXTO,
        font=fonte(12, True), corner_radius=10), kw))


def menu(master: Any, valores: list[str], variavel: Any = None, comando: Any = None, **kw: Any) -> ctk.CTkOptionMenu:
    return ctk.CTkOptionMenu(master, values=valores, variable=variavel, command=comando, **_mesclar(dict(
        fg_color=Cores.FUNDO, button_color=Cores.ROXO, button_hover_color=Cores.ROXO_HOVER,
        dropdown_fg_color=Cores.PAINEL, dropdown_hover_color=Cores.ROXO, text_color=Cores.TEXTO,
        dropdown_text_color=Cores.TEXTO, height=34, corner_radius=10, font=fonte(12)), kw))


# ============================================================================
# MÓDULO: gui.util
# ============================================================================
import sys
import threading
import time
from typing import Any, Callable, Optional

import cv2
import customtkinter as ctk
import numpy as np
from PIL import Image



class TabBase(ctk.CTkFrame):
    """Base das abas: ativar()/desativar() são chamados ao entrar/sair da aba."""

    def __init__(self, master: Any, app: Any) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app

    def ativar(self) -> None:
        """Chamado quando a aba fica visível."""

    def desativar(self) -> None:
        """Chamado quando a aba deixa de ficar visível."""


def ajustar(img: np.ndarray, largura: int, altura: int) -> np.ndarray:
    """Redimensiona mantendo proporção, centralizando sobre fundo preto-quente."""
    h, w = img.shape[:2]
    f = min(largura / w, altura / h)
    nova = cv2.resize(img, (max(1, int(w * f)), max(1, int(h * f))), interpolation=cv2.INTER_AREA)
    tela = np.full((altura, largura, 3), Cores.bgr(Cores.FUNDO), dtype=np.uint8)
    y, x = (altura - nova.shape[0]) // 2, (largura - nova.shape[1]) // 2
    tela[y:y + nova.shape[0], x:x + nova.shape[1]] = nova
    return tela


def para_ctk(bgr: np.ndarray, tamanho: tuple[int, int]) -> ctk.CTkImage:
    """BGR (OpenCV) -> CTkImage."""
    pil = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    return ctk.CTkImage(light_image=pil, dark_image=pil, size=tamanho)


def cinza_para_bgr(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR) if img.ndim == 2 else img


def desenhar_moldura(img: np.ndarray, cor: tuple[int, int, int], espessura: int = 4, comp: int = 46) -> None:
    """Moldura de câmera de segurança: cantos coloridos."""
    h, w = img.shape[:2]
    m = 8
    for (x, y, dx, dy) in ((m, m, 1, 1), (w - m, m, -1, 1), (m, h - m, 1, -1), (w - m, h - m, -1, -1)):
        cv2.line(img, (x, y), (x + dx * comp, y), cor, espessura)
        cv2.line(img, (x, y), (x, y + dy * comp), cor, espessura)


def executar_em_thread(widget: Any, funcao: Callable[[], Any], ao_concluir: Callable[[Any], None],
                       ao_erro: Optional[Callable[[Exception], None]] = None) -> None:
    """Roda `funcao` em thread e entrega o resultado na thread da GUI (sem travar a interface)."""
    caixa: dict[str, Any] = {}

    def alvo() -> None:
        try:
            caixa["ok"] = funcao()
        except Exception as exc:  # noqa: BLE001
            caixa["erro"] = exc
        caixa["fim"] = True

    def verificar() -> None:
        if not caixa.get("fim"):
            widget.after(60, verificar)
        elif "erro" in caixa:
            (ao_erro or (lambda e: None))(caixa["erro"])
        else:
            ao_concluir(caixa["ok"])

    threading.Thread(target=alvo, daemon=True).start()
    widget.after(60, verificar)


def tocar_alerta(widget: Any, vezes: int = 4) -> None:
    """Alerta sonoro (bipes no Windows; sino do Tk nos demais sistemas)."""
    if sys.platform.startswith("win"):
        def _bipes() -> None:
            import winsound
            for _ in range(vezes):
                winsound.Beep(1200, 250)
                time.sleep(0.05)
        threading.Thread(target=_bipes, daemon=True).start()
    else:
        for i in range(vezes):
            widget.after(i * 300, widget.bell)


def bip_sucesso(widget: Any) -> None:
    """Bipe curto de acesso concedido."""
    if sys.platform.startswith("win"):
        import winsound
        threading.Thread(target=lambda: winsound.Beep(880, 160), daemon=True).start()
    else:
        widget.bell()


def jpg_para_bgr(dados: bytes) -> Optional[np.ndarray]:
    arr = np.frombuffer(dados, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR) if arr.size else None


def bgr_para_jpg(img: np.ndarray, qualidade: int = 85) -> Optional[bytes]:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, qualidade])
    return buf.tobytes() if ok else None


# ============================================================================
# MÓDULO: gui.painel_fases
# ============================================================================
from typing import Any

import customtkinter as ctk


COR_STATUS = {"ok": Cores.ROXO_BRILHO, "aviso": Cores.ALERTA, "erro": Cores.ALERTA, "inativo": Cores.BORDA}
TXT_STATUS = {"ok": "OK", "aviso": "ATENÇÃO", "erro": "ERRO", "inativo": "—"}


class PainelFases(ctk.CTkFrame):
    """Cinco linhas: indicador colorido, nome da fase, detalhe e tempo em ms."""

    def __init__(self, master: Any, titulo: str = "Pipeline de processamento") -> None:
        super().__init__(master, fg_color=Cores.PAINEL, corner_radius=14, border_width=1, border_color=Cores.BORDA)
        rotulo(self, titulo, tam=13, negrito=True).pack(fill="x", padx=14, pady=(12, 4))
        self._linhas: dict[int, tuple[ctk.CTkLabel, ctk.CTkLabel, ctk.CTkLabel]] = {}
        for i in range(1, 6):
            linha = ctk.CTkFrame(self, fg_color="transparent")
            linha.pack(fill="x", padx=14, pady=3)
            linha.grid_columnconfigure(1, weight=1)
            ponto = ctk.CTkLabel(linha, text="●", text_color=Cores.BORDA, font=fonte(16), width=20)
            ponto.grid(row=0, column=0, rowspan=2, sticky="w")
            rotulo(linha, NOMES_FASES[i], tam=12, negrito=True).grid(row=0, column=1, sticky="w")
            detalhe = rotulo(linha, "—", secundario=True, tam=11)
            detalhe.grid(row=1, column=1, sticky="w")
            tempo = rotulo(linha, "", secundario=True, tam=11, anchor="e")
            tempo.grid(row=0, column=2, rowspan=2, sticky="e")
            self._linhas[i] = (ponto, detalhe, tempo)
        ctk.CTkFrame(self, fg_color="transparent", height=6).pack()

    def atualizar(self, res: ResultadoPipeline | None) -> None:
        """Atualiza as 5 linhas a partir do último resultado do pipeline."""
        for i, (ponto, detalhe, tempo) in self._linhas.items():
            st = res.status.get(i, "inativo") if res else "inativo"
            ponto.configure(text_color=COR_STATUS[st])
            txt = res.detalhes.get(i, "") if res else ""
            detalhe.configure(text=f"{TXT_STATUS[st]}" + (f" · {txt}" if txt else ""))
            ms = res.tempos_ms.get(i) if res else None
            tempo.configure(text=f"{ms:.0f} ms" if ms else "")


# ============================================================================
# MÓDULO: gui.tab_autenticacao
# ============================================================================
import logging
import time
from typing import Any, Optional

import cv2
import customtkinter as ctk
from PIL import Image


log = logging.getLogger(__name__)
AUT_LARG, AUT_ALT = 640, 480


class TabAutenticacao(TabBase):
    """Tela de autenticação biométrica facial."""

    def __init__(self, master: Any, app: Any) -> None:
        super().__init__(master, app)
        self._ativa = False
        self._job: Optional[str] = None
        self._n = 0
        self._ultima: Optional[Decisao] = None
        self._img_vazia = ctk.CTkImage(Image.new("RGB", (AUT_LARG, AUT_ALT), Cores.FUNDO), size=(AUT_LARG, AUT_ALT))
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ---- coluna esquerda: câmera
        esq = cartao(self)
        esq.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        self.lbl_video = ctk.CTkLabel(esq, text="", image=self._img_vazia, compound="center", width=AUT_LARG, height=AUT_ALT,
                                      text_color=Cores.TEXTO_SEC, font=fonte(14), wraplength=AUT_LARG - 80)
        self.lbl_video.pack(padx=16, pady=(16, 8))
        self.lbl_msg = rotulo(esq, "Aguardando...", tam=15, negrito=True, anchor="center", justify="center",
                              wraplength=AUT_LARG)
        self.lbl_msg.pack(fill="x", padx=16, pady=(4, 4))
        self.barra = ctk.CTkProgressBar(esq, progress_color=Cores.ROXO_BRILHO, fg_color=Cores.BORDA, height=10)
        self.barra.set(0)
        self.barra.pack(fill="x", padx=24, pady=(2, 6))
        self.lbl_info = rotulo(esq, "", secundario=True, tam=12, anchor="center", justify="center")
        self.lbl_info.pack(fill="x", padx=16, pady=(0, 14))

        # ---- coluna direita
        dir_ = ctk.CTkFrame(self, fg_color="transparent")
        dir_.grid(row=0, column=1, sticky="nsew")
        dir_.grid_columnconfigure(0, weight=1)

        c1 = cartao(dir_)
        c1.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        rotulo(c1, "Nível de acesso solicitado", tam=13, negrito=True).pack(fill="x", padx=14, pady=(12, 6))
        self.seg_nivel = segmentado(c1, ["Nível 1", "Nível 2", "Nível 3"], self._mudou_nivel)
        self.seg_nivel.set("Nível 1")
        self.seg_nivel.pack(fill="x", padx=14, pady=(0, 6))
        self.lbl_nivel = rotulo(c1, NIVEIS_NOMES[1], secundario=True, tam=11)
        self.lbl_nivel.pack(fill="x", padx=14, pady=(0, 6))
        self.lbl_tent = rotulo(c1, "", secundario=True, tam=12)
        self.lbl_tent.pack(fill="x", padx=14, pady=(0, 6))
        botao(c1, "Reiniciar câmera", self.app.reiniciar_camera, primario=False, height=30).pack(
            anchor="w", padx=14, pady=(0, 12))

        self.painel_res = ctk.CTkFrame(dir_, fg_color=Cores.PAINEL, corner_radius=14, border_width=2,
                                       border_color=Cores.BORDA)
        self.painel_res.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self.lbl_status = rotulo(self.painel_res, "AGUARDANDO", tam=24, negrito=True, anchor="center",
                                 justify="center", text_color=Cores.ROXO_BRILHO)
        self.lbl_status.pack(fill="x", padx=14, pady=(14, 2))
        self.lbl_quem = rotulo(self.painel_res, "—", tam=14, negrito=True, anchor="center", justify="center")
        self.lbl_quem.pack(fill="x", padx=14)
        self.lbl_det = rotulo(self.painel_res, "A decisão aparece aqui após ~2 s de rosto estável + piscada.",
                              secundario=True, tam=12, anchor="center", justify="center", wraplength=420)
        self.lbl_det.pack(fill="x", padx=14, pady=(2, 14))

        self.fases = PainelFases(dir_)
        self.fases.grid(row=2, column=0, sticky="ew")

    # ---------------------------------------------------------------- ciclo
    def ativar(self) -> None:
        self._ativa = True
        self.app.decisor.reiniciar()
        self.app.loop.nivel_solicitado = self._nivel()
        self._atualizar_tentativas()
        self._tick()

    def desativar(self) -> None:
        self._ativa = False
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None

    def _nivel(self) -> int:
        return int(self.seg_nivel.get()[-1])

    def _mudou_nivel(self, valor: str) -> None:
        n = int(valor[-1])
        self.lbl_nivel.configure(text=NIVEIS_NOMES[n])
        self.app.loop.nivel_solicitado = n

    def _atualizar_tentativas(self) -> None:
        self.lbl_tent.configure(text=f"Tentativas negadas: {self.app.bloqueio.tentativas}/{MAX_TENTATIVAS} "
                                     f"(bloqueio de {BLOQUEIO_S} s ao atingir o limite)")

    # ----------------------------------------------------------------- laço
    def _tick(self) -> None:
        if not self._ativa:
            return
        try:
            self._n += 1
            self._desenhar_video()
            self._atualizar_texto()
            if self._n % 3 == 0:
                self.fases.atualizar(self.app.loop.ultimo)
            while not self.app.loop.fila.empty():
                self._tratar_decisao(self.app.loop.fila.get_nowait())
        except Exception:  # noqa: BLE001
            log.exception("Erro ao atualizar a aba de autenticação")
        self._job = self.after(40, self._tick)

    def _desenhar_video(self) -> None:
        frame, _ = self.app.camera.ler_com_id()
        if frame is None:
            self.lbl_video.configure(image=self._img_vazia, text=self.app.camera.erro or "Aguardando a câmera...")
            return
        img = frame.copy()
        dec = self.app.decisor
        cor = Cores.bgr(Cores.ROXO_BRILHO)
        if dec.estado == "resultado" and dec.ultima_decisao:
            cor = Cores.bgr(Cores.VERDE if dec.ultima_decisao.concedido else Cores.VERMELHO)
        elif dec.estado == "bloqueado":
            cor = Cores.bgr(Cores.VERMELHO if int(time.time() * 2) % 2 else Cores.BORDA)
        res = self.app.loop.ultimo
        if res is not None and res.original is not None and time.time() - res.timestamp < 0.8 \
                and res.original.shape[:2] == img.shape[:2]:
            desenhar_rostos(img, res.rostos, cor)
        if img.shape[1] != AUT_LARG or img.shape[0] != AUT_ALT:
            img = cv2.resize(img, (AUT_LARG, AUT_ALT))
        desenhar_moldura(img, cor)
        self.lbl_video.configure(image=para_ctk(img, (AUT_LARG, AUT_ALT)), text="")

    def _atualizar_texto(self) -> None:
        dec = self.app.decisor
        res = self.app.loop.ultimo
        self.lbl_msg.configure(text=dec.mensagem if dec.estado != "aguardando" or not res or not res.erro
                               else res.mensagem)
        if dec.estado == "aguardando" and res is not None and res.erro:
            self.lbl_msg.configure(text=res.mensagem)
        self.barra.set(dec.progresso)
        restante = self.app.bloqueio.segundos_restantes()
        if restante > 0:
            pisca = int(time.time() * 2) % 2
            self.painel_res.configure(border_color=Cores.VERMELHO if pisca else Cores.BORDA)
            self.lbl_status.configure(text="BLOQUEADO", text_color=Cores.VERMELHO)
            self.lbl_quem.configure(text=f"Aguarde {restante} s")
            self.lbl_det.configure(text="Limite de tentativas negadas atingido. O sistema está temporariamente bloqueado.")
            self._atualizar_tentativas()
        elif dec.estado == "resultado" and dec.ultima_decisao is not None:
            pass  # mantém o painel do resultado
        else:
            self.painel_res.configure(border_color=Cores.BORDA)
            self.lbl_status.configure(text="ANALISANDO" if dec.estado in ("estabilizando", "aguardando_piscada")
                                      else "AGUARDANDO", text_color=Cores.ROXO_BRILHO)
            self.lbl_quem.configure(text="—")
            self.lbl_det.configure(text="A decisão aparece após ~2 s de rosto estável + piscada (vivacidade).")
        self.lbl_info.configure(text=f"Piscadas detectadas: {dec.piscadas}" if dec.estado in
                                ("estabilizando", "aguardando_piscada") else "")

    # --------------------------------------------------------------- decisão
    def _tratar_decisao(self, d: Decisao) -> None:
        self._ultima = d
        self.app.banco.registrar_log(d.usuario_id, d.nome, "CONCEDIDO" if d.concedido else "NEGADO",
                                     d.nivel_solicitado, d.confianca, d.motivo, d.captura_jpg)
        self._atualizar_tentativas()
        if d.concedido:
            usuario = self.app.banco.obter_usuario(d.usuario_id) if d.usuario_id else None
            if usuario is not None:
                self.app.definir_sessao(usuario, d.nivel_solicitado)
            self.painel_res.configure(border_color=Cores.VERDE)
            self.lbl_status.configure(text="ACESSO CONCEDIDO", text_color=Cores.VERDE)
            self.lbl_quem.configure(text=f"{d.nome} · {d.cargo}")
            self.lbl_det.configure(text=f"Nível {d.nivel_usuario} · {d.divisao or '—'} · confiança {d.confianca:.0f}% "
                                        f"(distância {d.distancia:.3f})\nAbrindo o cofre...")
            bip_sucesso(self)
            self.after(2500, lambda: self.app.ir_para("Cofre") if self.app.sessao_valida() else None)
        else:
            self.painel_res.configure(border_color=Cores.VERMELHO)
            self.lbl_status.configure(text="ACESSO NEGADO", text_color=Cores.VERMELHO)
            self.lbl_quem.configure(text=d.nome if d.nome != "desconhecido" else "Pessoa não identificada")
            self.lbl_det.configure(text=d.motivo)
            tocar_alerta(self, 6 if d.bloqueou else 2)


# ============================================================================
# MÓDULO: gui.tab_cadastro
# ============================================================================
import logging
import time
from tkinter import filedialog, messagebox
from typing import Any, Optional

import cv2
import customtkinter as ctk
import numpy as np
from PIL import Image


log = logging.getLogger(__name__)
CAD_LARG, CAD_ALT = 520, 390
ORIENTACOES = [
    "Amostra 1: olhe de frente, expressão neutra.",
    "Amostra 2: vire levemente o rosto para um lado.",
    "Amostra 3: vire levemente o rosto para o outro lado.",
    "Amostra 4: dê um sorriso natural.",
    "Amostra 5: incline levemente a cabeça para cima ou para baixo.",
]


class DialogoLGPD(ctk.CTkToplevel):
    """Janela com o aviso de privacidade completo."""

    def __init__(self, master: Any) -> None:
        super().__init__(master)
        self.title("Aviso de Privacidade — LGPD")
        self.geometry("720x560")
        self.configure(fg_color=Cores.FUNDO)
        caixa = ctk.CTkTextbox(self, fg_color=Cores.PAINEL, text_color=Cores.TEXTO, font=fonte(13), wrap="word",
                               border_width=1, border_color=Cores.BORDA)
        caixa.pack(fill="both", expand=True, padx=16, pady=(16, 8))
        caixa.insert("1.0", TEXTO_LGPD)
        caixa.configure(state="disabled")
        botao(self, "Fechar", self.destroy).pack(pady=(0, 16))
        self.transient(master)
        self.after(150, self._focar)

    def _focar(self) -> None:
        try:
            self.grab_set()
            self.focus()
        except Exception:  # noqa: BLE001
            pass


class TabCadastro(TabBase):
    """Cadastro de usuários (somente Ministro autenticado; ou primeiro cadastro)."""

    def __init__(self, master: Any, app: Any) -> None:
        super().__init__(master, app)
        self._ativa = False
        self._job: Optional[str] = None
        self.amostras: list[np.ndarray] = []
        self.foto: Optional[bytes] = None
        self._img_vazia = ctk.CTkImage(Image.new("RGB", (CAD_LARG, CAD_ALT), Cores.FUNDO), size=(CAD_LARG, CAD_ALT))
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0)
        self.grid_rowconfigure(0, weight=1)

        form = cartao(self)
        form.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        rotulo(form, "Novo usuário", tam=18, negrito=True).pack(fill="x", padx=18, pady=(16, 2))
        self.lbl_modo = rotulo(form, "", secundario=True, tam=12, wraplength=520)
        self.lbl_modo.pack(fill="x", padx=18, pady=(0, 8))
        rotulo(form, "Nome completo").pack(fill="x", padx=18)
        self.ent_nome = campo(form, "Ex.: Marina Silva")
        self.ent_nome.pack(fill="x", padx=18, pady=(2, 8))
        rotulo(form, "Cargo").pack(fill="x", padx=18)
        self.ent_cargo = campo(form, "Ex.: Diretora de Toxicologia")
        self.ent_cargo.pack(fill="x", padx=18, pady=(2, 8))
        rotulo(form, "Nível de acesso").pack(fill="x", padx=18)
        self.seg_nivel = segmentado(form, ["Nível 1", "Nível 2", "Nível 3"], self._mudou_nivel)
        self.seg_nivel.set("Nível 1")
        self.seg_nivel.pack(fill="x", padx=18, pady=(2, 8))
        rotulo(form, "Divisão (obrigatória no Nível 2)").pack(fill="x", padx=18)
        self.var_div = ctk.StringVar(value=DIVISOES[0])
        self.menu_div = menu(form, DIVISOES, self.var_div)
        self.menu_div.pack(fill="x", padx=18, pady=(2, 8))

        self.var_consent = ctk.BooleanVar(value=False)
        linha = ctk.CTkFrame(form, fg_color="transparent")
        linha.pack(fill="x", padx=18, pady=(4, 8))
        ctk.CTkCheckBox(linha, text="Li e concordo com o Aviso de Privacidade (LGPD) e consinto com o tratamento "
                        "dos dados biométricos.", variable=self.var_consent, fg_color=Cores.ROXO,
                        hover_color=Cores.ROXO_HOVER, border_color=Cores.BORDA, text_color=Cores.TEXTO,
                        font=fonte(12)).pack(side="left", fill="x", expand=True)
        botao(linha, "Ler aviso", lambda: DialogoLGPD(self.winfo_toplevel()), primario=False, width=90,
              height=30).pack(side="right")

        self.lbl_amostras = rotulo(form, "", tam=14, negrito=True)
        self.lbl_amostras.pack(fill="x", padx=18, pady=(6, 0))
        self.barra = ctk.CTkProgressBar(form, progress_color=Cores.ROXO_BRILHO, fg_color=Cores.BORDA, height=10)
        self.barra.pack(fill="x", padx=18, pady=(4, 4))
        self.lbl_orient = rotulo(form, "", secundario=True, tam=12, wraplength=520)
        self.lbl_orient.pack(fill="x", padx=18, pady=(0, 6))
        self.lbl_aviso = rotulo(form, "", tam=12, wraplength=520, text_color=Cores.ALERTA)
        self.lbl_aviso.pack(fill="x", padx=18)
        botoes = ctk.CTkFrame(form, fg_color="transparent")
        botoes.pack(fill="x", padx=18, pady=(8, 4))
        self.btn_cap = botao(botoes, "Capturar amostra (webcam)", self._capturar)
        self.btn_cap.pack(side="left", padx=(0, 8))
        self.btn_arq = botao(botoes, "Carregar imagem...", self._carregar_arquivo, primario=False)
        self.btn_arq.pack(side="left", padx=(0, 8))
        botao(botoes, "Refazer", self._refazer, primario=False, width=80).pack(side="left")
        self.btn_ok = botao(form, "Concluir cadastro", self._concluir, state="disabled")
        self.btn_ok.pack(fill="x", padx=18, pady=(6, 16))

        cam = cartao(self)
        cam.grid(row=0, column=1, sticky="n")
        self.lbl_video = ctk.CTkLabel(cam, text="", image=self._img_vazia, compound="center", width=CAD_LARG,
                                      height=CAD_ALT, text_color=Cores.TEXTO_SEC, font=fonte(13), wraplength=CAD_LARG - 60)
        self.lbl_video.pack(padx=14, pady=(14, 6))
        self.lbl_msg = rotulo(cam, "", tam=13, negrito=True, anchor="center", justify="center", wraplength=CAD_LARG)
        self.lbl_msg.pack(fill="x", padx=14, pady=(0, 14))

        self.aviso = ctk.CTkFrame(self, fg_color=Cores.PAINEL, corner_radius=14, border_width=1,
                                  border_color=Cores.BORDA)
        rotulo(self.aviso, "🔒  Acesso restrito", tam=22, negrito=True, anchor="center", justify="center").place(
            relx=0.5, rely=0.42, anchor="center")
        rotulo(self.aviso, "O cadastro de usuários só pode ser feito pelo Ministro (nível 3) autenticado.\n"
               "Vá até a aba Autenticação, solicite o Nível 3 e valide o seu rosto.", secundario=True, tam=14,
               anchor="center", justify="center").place(relx=0.5, rely=0.52, anchor="center")
        self._refazer()
        self._mudou_nivel("Nível 1")

    # ------------------------------------------------------------ permissões
    def _modo(self) -> str:
        if not self.app.banco.existe_ministro():
            return "primeiro"
        return "ok" if self.app.sessao_valida(3) else "negado"

    def ativar(self) -> None:
        self._ativa = True
        modo = self._modo()
        if modo == "negado":
            self.aviso.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.aviso.lift()
        else:
            self.aviso.place_forget()
        if modo == "primeiro":
            self.seg_nivel.set("Nível 3")
            self.seg_nivel.configure(state="disabled")
            self._mudou_nivel("Nível 3")
            self.lbl_modo.configure(text="PRIMEIRO USO: o primeiro cadastro define o Ministro (nível 3), "
                                         "administrador do sistema.")
        else:
            self.seg_nivel.configure(state="normal")
            self.lbl_modo.configure(text="Cadastro realizado pelo Ministro autenticado.")
        self._tick()

    def desativar(self) -> None:
        self._ativa = False
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None

    def _mudou_nivel(self, valor: str) -> None:
        self.menu_div.configure(state="normal" if valor.endswith("2") else "disabled")

    # ------------------------------------------------------------------ vídeo
    def _tick(self) -> None:
        if not self._ativa:
            return
        try:
            frame, _ = self.app.camera.ler_com_id()
            if frame is None:
                self.lbl_video.configure(image=self._img_vazia, text=self.app.camera.erro or "Aguardando a câmera...")
            else:
                img = frame.copy()
                res = self.app.loop.ultimo
                cor = Cores.bgr(Cores.ROXO_BRILHO)
                if res is not None and res.original is not None and time.time() - res.timestamp < 0.8 \
                        and res.original.shape[:2] == img.shape[:2]:
                    desenhar_rostos(img, res.rostos, cor)
                    self.lbl_msg.configure(text=res.mensagem if res.erro else "Rosto OK — pronto para capturar.")
                img = cv2.resize(img, (CAD_LARG, CAD_ALT))
                desenhar_moldura(img, cor, 3, 36)
                self.lbl_video.configure(image=para_ctk(img, (CAD_LARG, CAD_ALT)), text="")
        except Exception:  # noqa: BLE001
            log.exception("Erro na aba de cadastro")
        self._job = self.after(50, self._tick)

    # ---------------------------------------------------------------- amostras
    def _refazer(self) -> None:
        self.amostras, self.foto = [], None
        self._atualizar_amostras()

    def _atualizar_amostras(self) -> None:
        n = len(self.amostras)
        self.lbl_amostras.configure(text=f"Amostras: {n}/{N_AMOSTRAS}")
        self.barra.set(n / N_AMOSTRAS)
        self.lbl_orient.configure(text=ORIENTACOES[n] if n < N_AMOSTRAS else "Todas as amostras capturadas. "
                                                                              "Revise os dados e conclua.")
        self.btn_ok.configure(state="normal" if n >= N_AMOSTRAS else "disabled")
        self.btn_cap.configure(state="normal" if n < N_AMOSTRAS else "disabled")
        self.btn_arq.configure(state="normal" if n < N_AMOSTRAS else "disabled")

    def _adicionar(self, enc: np.ndarray, crop: Optional[np.ndarray]) -> None:
        if self.amostras and float(np.linalg.norm(self.amostras[0] - enc)) > DISTANCIA_MAX_ENTRE_AMOSTRAS:
            self.lbl_aviso.configure(text="Esta amostra parece ser de OUTRA pessoa. Use sempre o mesmo rosto.")
            return
        self.lbl_aviso.configure(text="")
        if not self.amostras and crop is not None:
            self.foto = bgr_para_jpg(cv2.resize(crop, (160, 160)))
        self.amostras.append(enc)
        self._atualizar_amostras()

    def _capturar(self) -> None:
        res = self.app.loop.ultimo
        if self.app.loop.modo != "cadastro" or res is None or time.time() - res.timestamp > 1.0:
            self.lbl_aviso.configure(text="Sem imagem recente da câmera. Verifique a câmera e tente novamente.")
            return
        if res.erro or res.encoding is None:
            self.lbl_aviso.configure(text=res.mensagem or "Não foi possível capturar esta amostra.")
            return
        self._adicionar(res.encoding, res.crop)

    def _carregar_arquivo(self) -> None:
        caminho = filedialog.askopenfilename(title="Escolha uma foto do rosto", filetypes=[
            ("Imagens", "*.jpg *.jpeg *.png *.bmp"), ("Todos", "*.*")])
        if not caminho:
            return
        self.lbl_aviso.configure(text="Processando imagem...")

        def tarefa():
            img = carregar_imagem(caminho)
            return processar_quadro(img, self.app.ajustes, None, codificar=True, num_jitters=2)

        def pronto(res) -> None:
            if res.erro or res.encoding is None:
                self.lbl_aviso.configure(text=res.mensagem or "Não foi possível extrair o rosto desta imagem.")
            else:
                self._adicionar(res.encoding, res.crop)

        def erro(exc: Exception) -> None:
            self.lbl_aviso.configure(text=str(exc) if isinstance(exc, ErroImagem) else f"Falha: {exc}")

        executar_em_thread(self, tarefa, pronto, erro)

    # ---------------------------------------------------------------- concluir
    def _concluir(self) -> None:
        nome, cargo = self.ent_nome.get().strip(), self.ent_cargo.get().strip()
        nivel = int(self.seg_nivel.get()[-1])
        if len(nome) < 3 or len(cargo) < 2:
            messagebox.showwarning("Dados incompletos", "Preencha nome e cargo.")
            return
        if not self.var_consent.get():
            messagebox.showwarning("Consentimento", "É obrigatório aceitar o Aviso de Privacidade (LGPD).")
            return
        if len(self.amostras) < N_AMOSTRAS:
            messagebox.showwarning("Amostras", f"Capture as {N_AMOSTRAS} amostras.")
            return
        modo = self._modo()
        if modo == "negado":
            messagebox.showerror("Acesso negado", "Sessão de Ministro ausente ou expirada.")
            self.ativar()
            return
        if modo == "primeiro":
            nivel = 3
        divisao = self.var_div.get() if nivel == 2 else (DIVISAO_MINISTRO if nivel == 3 else DIVISAO_GERAL)
        media = np.mean(np.vstack(self.amostras), axis=0)
        dup = self.app.classificador.encontrar_duplicado(media, self.app.ajustes.tolerancia)
        if dup is not None:
            messagebox.showerror("Rosto já cadastrado", f"Este rosto já pertence a '{dup.nome}'. "
                                                        "Não é permitido cadastrar a mesma pessoa duas vezes.")
            return
        try:
            self.app.banco.criar_usuario(nome, cargo, nivel, divisao, media, self.foto)
        except ErroBanco as exc:
            messagebox.showerror("Não foi possível cadastrar", str(exc))
            return
        self.app.banco.registrar_log(self.app.sessao.usuario.id if self.app.sessao else None,
                                     self.app.sessao.usuario.nome if self.app.sessao else "sistema", "ADMIN", nivel,
                                     None, f"Cadastro do usuário '{nome}' (nível {nivel}, {divisao}).")
        self.app.recarregar_classificador()
        messagebox.showinfo("Cadastro concluído", f"'{nome}' foi cadastrado(a) com nível {nivel}.\n"
                                                  "Os dados faciais foram armazenados criptografados.")
        self.ent_nome.delete(0, "end")
        self.ent_cargo.delete(0, "end")
        self.var_consent.set(False)
        self._refazer()
        self.ativar()


# ============================================================================
# MÓDULO: gui.tab_pipeline
# ============================================================================
import logging
import time
from typing import Any, Optional

import cv2
import customtkinter as ctk
import numpy as np
from PIL import Image


log = logging.getLogger(__name__)
TW, TH = 300, 225


class Miniatura(ctk.CTkFrame):
    """Imagem com título."""

    def __init__(self, master: Any, titulo: str, w: int = TW, h: int = TH) -> None:
        super().__init__(master, fg_color=Cores.PAINEL, corner_radius=12, border_width=1, border_color=Cores.BORDA)
        self.w, self.h = w, h
        rotulo(self, titulo, tam=12, negrito=True).pack(fill="x", padx=10, pady=(8, 2))
        self._vazia = ctk.CTkImage(Image.new("RGB", (w, h), Cores.FUNDO), size=(w, h))
        self.img = ctk.CTkLabel(self, text="", image=self._vazia, width=w, height=h)
        self.img.pack(padx=10, pady=(0, 10))

    def mostrar(self, bgr: Optional[np.ndarray]) -> None:
        self.img.configure(image=self._vazia if bgr is None else para_ctk(ajustar(cinza_para_bgr(bgr), self.w, self.h),
                                                                         (self.w, self.h)))


class TabPipeline(TabBase):
    """Mostra o que cada fase produz, em tempo real."""

    def __init__(self, master: Any, app: Any) -> None:
        super().__init__(master, app)
        self._ativa = False
        self._job: Optional[str] = None
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        esq = ctk.CTkFrame(self, fg_color="transparent")
        esq.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        self.fases = PainelFases(esq, "Status ao vivo das fases")
        self.fases.pack(fill="x", pady=(0, 10), ipadx=40)
        info = cartao(esq)
        info.pack(fill="x")
        rotulo(info, "Fase 5 · Classificação", tam=13, negrito=True).pack(fill="x", padx=14, pady=(12, 4))
        self.lbl_cls = rotulo(info, "—", secundario=True, tam=12, wraplength=300)
        self.lbl_cls.pack(fill="x", padx=14, pady=(0, 6))
        self.lbl_q = rotulo(info, "", secundario=True, tam=12, wraplength=300)
        self.lbl_q.pack(fill="x", padx=14, pady=(0, 12))
        rotulo(esq, "Esta aba só analisa: não concede acesso.", secundario=True, tam=11, wraplength=300).pack(
            fill="x", pady=(10, 0))

        dir_ = ctk.CTkFrame(self, fg_color="transparent")
        dir_.grid(row=0, column=1, sticky="nsew")
        for c in range(3):
            dir_.grid_columnconfigure(c, weight=1)
        self.m_orig = Miniatura(dir_, "ANTES · 1 Aquisição (original)")
        self.m_cinza = Miniatura(dir_, "2 · Escala de cinza")
        self.m_clahe = Miniatura(dir_, "2 · CLAHE (equalização)")
        self.m_filtro = Miniatura(dir_, "2 · Filtro de ruído")
        self.m_norm = Miniatura(dir_, "DEPOIS · iluminação normalizada")
        self.m_rosto = Miniatura(dir_, "3 · Segmentação (caixa + landmarks)")
        for i, m in enumerate((self.m_orig, self.m_cinza, self.m_clahe, self.m_filtro, self.m_norm, self.m_rosto)):
            m.grid(row=i // 3, column=i % 3, padx=5, pady=5, sticky="nsew")
        enc = cartao(dir_)
        enc.grid(row=2, column=0, columnspan=3, sticky="ew", padx=5, pady=5)
        rotulo(enc, "4 · Extração de características — vetor facial de 128 dimensões", tam=12, negrito=True).pack(
            fill="x", padx=10, pady=(8, 2))
        self._vazia = ctk.CTkImage(Image.new("RGB", (900, 90), Cores.FUNDO), size=(900, 90))
        self.img_enc = ctk.CTkLabel(enc, text="", image=self._vazia, width=900, height=90)
        self.img_enc.pack(padx=10, pady=(0, 10))

    def ativar(self) -> None:
        self._ativa = True
        self._tick()

    def desativar(self) -> None:
        self._ativa = False
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None

    def _tick(self) -> None:
        if not self._ativa:
            return
        try:
            res = self.app.loop.ultimo
            if res is not None and res.original is not None and time.time() - res.timestamp < 2.0:
                self.fases.atualizar(res)
                self.m_orig.mostrar(res.original)
                if res.pre is not None:
                    self.m_cinza.mostrar(res.pre.cinza)
                    self.m_clahe.mostrar(res.pre.clahe)
                    self.m_filtro.mostrar(res.pre.filtrado)
                    self.m_norm.mostrar(res.pre.normalizado)
                    if res.rostos:
                        marcada = res.pre.normalizado.copy()
                        desenhar_rostos(marcada, res.rostos)
                        self.m_rosto.mostrar(recortar_rosto(marcada, res.rostos[0].caixa, 0.4))
                    else:
                        self.m_rosto.mostrar(None)
                if res.encoding is not None:
                    self.img_enc.configure(image=para_ctk(visualizar_encoding(res.encoding, 900, 90), (900, 90)))
                c = res.classificacao
                if c is not None:
                    quem = c.nome if c.identificado else "desconhecido"
                    self.lbl_cls.configure(text=f"Candidato: {quem}\nDistância euclidiana: {c.distancia:.3f}\n"
                                                f"Confiança: {c.confianca:.0f}%  ·  Nível: {c.nivel or '—'}")
                else:
                    self.lbl_cls.configure(text=res.mensagem or "—")
                self.lbl_q.configure(text=f"Luminosidade: {res.luminosidade:.0f}/255 · Nitidez: {res.nitidez:.0f}")
            else:
                self.fases.atualizar(None)
                self.lbl_cls.configure(text=self.app.camera.erro or "Aguardando imagens da câmera...")
        except Exception:  # noqa: BLE001
            log.exception("Erro na aba Pipeline")
        self._job = self.after(150, self._tick)


# ============================================================================
# MÓDULO: gui.tab_cofre
# ============================================================================
from typing import Any, Optional

import customtkinter as ctk



class TabCofre(TabBase):
    """Interior do cofre: o usuário só vê o que o nível efetivo da sessão permite."""

    def __init__(self, master: Any, app: Any) -> None:
        super().__init__(master, app)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.lista = cartao(self, width=360)
        self.lista.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        self.lista.grid_propagate(False)
        self.lbl_quem = rotulo(self.lista, "", tam=14, negrito=True, wraplength=320)
        self.lbl_quem.pack(fill="x", padx=14, pady=(14, 2))
        self.lbl_nivel = rotulo(self.lista, "", secundario=True, tam=12, wraplength=320)
        self.lbl_nivel.pack(fill="x", padx=14, pady=(0, 8))
        self.docs = ctk.CTkScrollableFrame(self.lista, fg_color="transparent", height=300)
        self.docs.pack(fill="both", expand=True, padx=8)
        self.restrito = ctk.CTkFrame(self.lista, fg_color="transparent")
        self.restrito.pack(fill="x", padx=14, pady=(6, 14))

        self.leitor = cartao(self)
        self.leitor.grid(row=0, column=1, sticky="nsew")
        self.lbl_titulo = rotulo(self.leitor, "Selecione um documento", tam=20, negrito=True, wraplength=760)
        self.lbl_titulo.pack(fill="x", padx=20, pady=(18, 2))
        self.lbl_meta = rotulo(self.leitor, "", secundario=True, tam=12)
        self.lbl_meta.pack(fill="x", padx=20)
        self.banner = ctk.CTkLabel(self.leitor, text="", font=fonte(15, True), corner_radius=10, height=44,
                                   text_color="#FFFFFF", wraplength=760)
        self.texto = ctk.CTkTextbox(self.leitor, fg_color=Cores.FUNDO, text_color=Cores.TEXTO, font=fonte(14),
                                    wrap="word", border_width=1, border_color=Cores.BORDA)
        self.texto.pack(fill="both", expand=True, padx=20, pady=(10, 20))
        self.texto.configure(state="disabled")

        self.bloq = ctk.CTkFrame(self, fg_color=Cores.PAINEL, corner_radius=14, border_width=1,
                                 border_color=Cores.BORDA)
        rotulo(self.bloq, "🔒  Cofre trancado", tam=22, negrito=True, anchor="center", justify="center").place(
            relx=0.5, rely=0.42, anchor="center")
        rotulo(self.bloq, "Autentique-se na aba Autenticação para abrir o cofre.", secundario=True, tam=14,
               anchor="center", justify="center").place(relx=0.5, rely=0.52, anchor="center")

    def ativar(self) -> None:
        if not self.app.sessao_valida():
            self.bloq.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.bloq.lift()
            return
        self.bloq.place_forget()
        s = self.app.sessao
        u = s.usuario
        self.lbl_quem.configure(text=f"{u.nome}\n{u.cargo}")
        div = f" · {u.divisao}" if s.nivel_efetivo == 2 else ""
        self.lbl_nivel.configure(text=f"Acesso efetivo: Nível {s.nivel_efetivo}{div}")
        for w in list(self.docs.winfo_children()) + list(self.restrito.winfo_children()):
            w.destroy()
        for d in documentos_visiveis(s.nivel_efetivo, u.divisao):
            botao(self.docs, f"{d.id} · {d.titulo}", lambda doc=d: self._abrir(doc), primario=False, anchor="w",
                  height=44).pack(fill="x", pady=3)
        alvo = [n for n in (2, 3) if n > s.nivel_efetivo]
        if alvo:
            rotulo(self.restrito, "Áreas restritas (tentativas são registradas):", secundario=True, tam=11).pack(
                fill="x", pady=(0, 4))
        for n in alvo:
            botao(self.restrito, f"Tentar acessar compartimento de Nível {n}",
                  lambda nv=n: self._tentar_restrito(nv), primario=False, height=32,
                  text_color=Cores.TEXTO_SEC).pack(fill="x", pady=2)
        if s.nivel_efetivo == 2:
            botao(self.restrito, "Tentar acessar divisão de outra área", self._tentar_outra_divisao, primario=False,
                  height=32, text_color=Cores.TEXTO_SEC).pack(fill="x", pady=2)
        self._mostrar("Selecione um documento", "", "")

    # -------------------------------------------------------------- leitura
    def _mostrar(self, titulo: str, meta: str, corpo: str) -> None:
        self.banner.pack_forget()
        self.lbl_titulo.configure(text=titulo)
        self.lbl_meta.configure(text=meta)
        self.texto.configure(state="normal")
        self.texto.delete("1.0", "end")
        self.texto.insert("1.0", corpo)
        self.texto.configure(state="disabled")

    def _abrir(self, doc: Documento) -> None:
        s = self.app.sessao
        if not self.app.sessao_valida() or s is None:
            self.ativar()
            return
        ok, motivo = pode_acessar(doc, s.nivel_efetivo, s.usuario.divisao)
        if not ok:  # defesa em profundidade
            self._negar(doc.nivel, f"Tentativa de abrir {doc.id}: {motivo}")
            return
        self._mostrar(doc.titulo, f"{doc.id} · Nível {doc.nivel}" + (f" · {doc.divisao}" if doc.divisao else ""),
                      doc.conteudo)
        self.app.banco.registrar_log(s.usuario.id, s.usuario.nome, "CONSULTA", doc.nivel, None,
                                     f"Documento {doc.id} consultado.")

    def _tentar_restrito(self, nivel: int) -> None:
        alvo = next(d for d in DOCUMENTOS if d.nivel == nivel)
        self._negar(nivel, f"Tentativa de acessar compartimento de nível {nivel} (ex.: {alvo.id}) "
                           f"com nível efetivo {self.app.sessao.nivel_efetivo}.")

    def _tentar_outra_divisao(self) -> None:
        s = self.app.sessao
        outro = next(d for d in DOCUMENTOS if d.nivel == 2 and d.divisao.lower() != s.usuario.divisao.lower())
        self._negar(2, f"Tentativa de acessar {outro.id} ({outro.divisao}) por diretor da divisão '{s.usuario.divisao}'.")

    def _negar(self, nivel: int, motivo: str) -> None:
        s = self.app.sessao
        if s is None:
            return
        self.app.banco.registrar_log(s.usuario.id, s.usuario.nome, "NEGADO", nivel, None, motivo)
        self._mostrar("ACESSO NEGADO", "", "Seu nível de acesso não permite abrir este conteúdo.\n\n"
                                           "A tentativa foi registrada no log de auditoria.\n\n" + motivo)
        self.banner.configure(text="ACESSO NEGADO — tentativa registrada", fg_color=Cores.VERMELHO)
        self.banner.pack(fill="x", padx=20, pady=(8, 0), before=self.texto)
        tocar_alerta(self, 2)


# ============================================================================
# MÓDULO: gui.tab_logs
# ============================================================================
from tkinter import ttk
from typing import Any, Optional

import customtkinter as ctk
from PIL import Image


COLUNAS = [("id", "ID", 55), ("data", "Data/hora", 150), ("usuario", "Usuário", 170), ("resultado", "Resultado", 100),
           ("nivel", "Nível", 55), ("conf", "Conf.", 65), ("motivo", "Motivo", 420)]
FILTROS = {"Todos": None, "Concedidos": "CONCEDIDO", "Negados": "NEGADO", "Outros eventos": "OUTROS"}


class TabLogs(TabBase):
    """Consulta ao log de auditoria (somente leitura)."""

    def __init__(self, master: Any, app: Any) -> None:
        super().__init__(master, app)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        topo = cartao(self)
        topo.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self.seg = segmentado(topo, list(FILTROS), lambda v: self.atualizar())
        self.seg.set("Todos")
        self.seg.pack(side="left", padx=14, pady=12)
        self.ent_busca = campo(topo, "Buscar por usuário ou motivo...", width=280)
        self.ent_busca.pack(side="left", padx=6)
        self.ent_busca.bind("<Return>", lambda e: self.atualizar())
        botao(topo, "Atualizar", self.atualizar, width=100).pack(side="left", padx=6)
        self.lbl_total = rotulo(topo, "", secundario=True, tam=12, anchor="e")
        self.lbl_total.pack(side="right", padx=14)

        quadro = cartao(self)
        quadro.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        quadro.grid_columnconfigure(0, weight=1)
        quadro.grid_rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(quadro, columns=[c[0] for c in COLUNAS], show="headings", selectmode="browse")
        for chave, titulo, larg in COLUNAS:
            self.tree.heading(chave, text=titulo)
            self.tree.column(chave, width=larg, anchor="w" if chave in ("usuario", "motivo") else "center",
                             stretch=chave == "motivo")
        self.tree.tag_configure("CONCEDIDO", foreground=Cores.VERDE)
        self.tree.tag_configure("NEGADO", foreground=Cores.VERMELHO)
        self.tree.tag_configure("OUTRO", foreground=Cores.TEXTO_SEC)
        sb = ttk.Scrollbar(quadro, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        sb.grid(row=0, column=1, sticky="ns", pady=8, padx=(0, 6))
        self.tree.bind("<<TreeviewSelect>>", self._selecionou)

        lado = cartao(self, width=320)
        lado.grid(row=1, column=1, sticky="ns")
        lado.grid_propagate(False)
        rotulo(lado, "Captura da tentativa", tam=14, negrito=True).pack(fill="x", padx=14, pady=(14, 6))
        self._vazia = ctk.CTkImage(Image.new("RGB", (280, 210), Cores.FUNDO), size=(280, 210))
        self.img = ctk.CTkLabel(lado, text="", image=self._vazia, width=280, height=210)
        self.img.pack(padx=14)
        self.lbl_det = rotulo(lado, "Selecione um registro.", secundario=True, tam=12, wraplength=290)
        self.lbl_det.pack(fill="x", padx=14, pady=10)

    def ativar(self) -> None:
        self.atualizar()

    def atualizar(self) -> None:
        filtro = FILTROS.get(self.seg.get())
        try:
            linhas = self.app.banco.listar_logs(filtro, self.ent_busca.get(), 500)
        except Exception as exc:  # noqa: BLE001
            self.lbl_total.configure(text=f"Erro ao ler logs: {exc}")
            return
        self.tree.delete(*self.tree.get_children())
        for r in linhas:
            tag = r["resultado"] if r["resultado"] in ("CONCEDIDO", "NEGADO") else "OUTRO"
            conf = "" if r["confianca"] is None else f"{r['confianca']:.0f}%"
            self.tree.insert("", "end", iid=str(r["id"]), tags=(tag,), values=(
                r["id"], r["data_hora"], r["usuario_nome"], r["resultado"], r["nivel_solicitado"] or "—", conf,
                r["motivo"]))
        self.lbl_total.configure(text=f"{len(linhas)} registro(s) (máx. 500 mais recentes)")

    def _selecionou(self, _evt: Any = None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        valores = self.tree.item(sel[0], "values")
        self.lbl_det.configure(text=f"{valores[1]}\n{valores[2]} · {valores[3]}\n{valores[6]}")
        dados = self.app.banco.obter_captura(int(sel[0]))
        img = jpg_para_bgr(dados) if dados else None
        if img is None:
            self.img.configure(image=self._vazia)
        else:
            self.img.configure(image=para_ctk(ajustar(img, 280, 210), (280, 210)))


# ============================================================================
# MÓDULO: gui.tab_relatorios
# ============================================================================
import logging
from datetime import datetime
from tkinter import filedialog, messagebox
from typing import Any, Optional

import customtkinter as ctk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


log = logging.getLogger(__name__)


class TabRelatorios(TabBase):
    """Relatórios de acesso."""

    def __init__(self, master: Any, app: Any) -> None:
        super().__init__(master, app)
        self.est: Optional[Estatisticas] = None
        self._canvases: list[FigureCanvasTkAgg] = []
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        topo = cartao(self)
        topo.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        rotulo(topo, "Período:", tam=13, negrito=True).pack(side="left", padx=(14, 6), pady=12)
        self.var_periodo = ctk.StringVar(value="Últimos 30 dias")
        menu(topo, list(PERIODOS), self.var_periodo, lambda v: self.atualizar(), width=180).pack(side="left")
        botao(topo, "Atualizar", self.atualizar, width=100).pack(side="left", padx=8)
        botao(topo, "Exportar PDF", self._pdf, primario=False, width=120).pack(side="right", padx=(6, 14))
        botao(topo, "Exportar CSV", self._csv, primario=False, width=120).pack(side="right")

        self.kpis = ctk.CTkFrame(self, fg_color="transparent")
        self.kpis.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self._kpi: dict[str, ctk.CTkLabel] = {}
        for i, (chave, titulo, cor) in enumerate([("total", "Total de acessos", Cores.ROXO_BRILHO),
                                                  ("c", "Concedidos", Cores.VERDE), ("n", "Negados", Cores.VERMELHO),
                                                  ("ts", "Taxa de sucesso", Cores.ROXO_BRILHO),
                                                  ("tn", "Taxa de negação", Cores.ROXO_BRILHO)]):
            self.kpis.grid_columnconfigure(i, weight=1)
            c = cartao(self.kpis)
            c.grid(row=0, column=i, padx=4, sticky="ew")
            rotulo(c, titulo, secundario=True, tam=12).pack(fill="x", padx=14, pady=(10, 0))
            self._kpi[chave] = rotulo(c, "0", tam=26, negrito=True, text_color=cor)
            self._kpi[chave].pack(fill="x", padx=14, pady=(0, 10))

        self.area = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.area.grid(row=2, column=0, sticky="nsew")
        self.area.grid_columnconfigure((0, 1), weight=1)
        self.celulas = [cartao(self.area) for _ in range(4)]
        for i, c in enumerate(self.celulas):
            c.grid(row=i // 2, column=i % 2, padx=4, pady=4, sticky="nsew")

    def ativar(self) -> None:
        self.atualizar()

    def atualizar(self) -> None:
        try:
            self.est = coletar(self.app.banco, self.var_periodo.get())
        except Exception as exc:  # noqa: BLE001
            log.exception("Falha ao calcular estatísticas")
            messagebox.showerror("Relatórios", f"Não foi possível calcular as estatísticas: {exc}")
            return
        e = self.est
        self._kpi["total"].configure(text=str(e.total))
        self._kpi["c"].configure(text=str(e.concedidos))
        self._kpi["n"].configure(text=str(e.negados))
        self._kpi["ts"].configure(text=f"{e.taxa_sucesso:.1f}%")
        self._kpi["tn"].configure(text=f"{e.taxa_negacao:.1f}%")
        for cv in self._canvases:
            cv.get_tk_widget().destroy()
        self._canvases = []
        for celula, fabrica in zip(self.celulas, (grafico_por_dia, grafico_por_nivel,
                                                  grafico_top_usuarios, grafico_resultado)):
            canvas = FigureCanvasTkAgg(fabrica(e, True), master=celula)
            canvas.draw()
            canvas.get_tk_widget().configure(height=270, highlightthickness=0, bg=Cores.PAINEL)
            canvas.get_tk_widget().pack(fill="both", expand=True, padx=6, pady=6)
            self._canvases.append(canvas)

    def _csv(self) -> None:
        destino = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV", "*.csv")],
                                               initialfile=f"auditoria_{datetime.now():%Y%m%d_%H%M}.csv")
        if not destino:
            return
        try:
            n = exportar_csv(self.app.banco, self.var_periodo.get(), destino)
            messagebox.showinfo("Exportação concluída", f"{n} registro(s) exportado(s).")
        except OSError as exc:
            messagebox.showerror("Erro ao exportar", f"Não foi possível salvar o arquivo: {exc}")

    def _pdf(self) -> None:
        if self.est is None:
            self.atualizar()
        destino = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF", "*.pdf")],
                                               initialfile=f"relatorio_cofre_{datetime.now():%Y%m%d_%H%M}.pdf")
        if not destino or self.est is None:
            return
        try:
            exportar_pdf(self.app.banco, self.est, destino)
            messagebox.showinfo("Exportação concluída", "Relatório em PDF gerado com sucesso.")
        except Exception as exc:  # noqa: BLE001
            log.exception("Falha ao gerar PDF")
            messagebox.showerror("Erro ao gerar PDF", str(exc))


# ============================================================================
# MÓDULO: gui.tab_admin
# ============================================================================
import logging
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Optional

import customtkinter as ctk


log = logging.getLogger(__name__)


class DialogoEdicao(ctk.CTkToplevel):
    """Edição de nome, cargo, nível e divisão de um usuário."""

    def __init__(self, master: Any, usuario: Usuario, ao_salvar: Any) -> None:
        super().__init__(master)
        self.usuario, self.ao_salvar = usuario, ao_salvar
        self.title("Editar usuário")
        self.geometry("460x470")
        self.configure(fg_color=Cores.FUNDO)
        rotulo(self, f"Editar: {usuario.nome}", tam=17, negrito=True).pack(fill="x", padx=20, pady=(18, 8))
        rotulo(self, "Nome").pack(fill="x", padx=20)
        self.e_nome = campo(self)
        self.e_nome.insert(0, usuario.nome)
        self.e_nome.pack(fill="x", padx=20, pady=(2, 8))
        rotulo(self, "Cargo").pack(fill="x", padx=20)
        self.e_cargo = campo(self)
        self.e_cargo.insert(0, usuario.cargo)
        self.e_cargo.pack(fill="x", padx=20, pady=(2, 8))
        rotulo(self, "Nível").pack(fill="x", padx=20)
        self.seg = segmentado(self, ["Nível 1", "Nível 2", "Nível 3"], self._mudou)
        self.seg.set(f"Nível {usuario.nivel}")
        self.seg.pack(fill="x", padx=20, pady=(2, 8))
        rotulo(self, "Divisão (Nível 2)").pack(fill="x", padx=20)
        self.var_div = ctk.StringVar(value=usuario.divisao if usuario.divisao in DIVISOES else DIVISOES[0])
        self.menu_div = menu(self, DIVISOES, self.var_div)
        self.menu_div.pack(fill="x", padx=20, pady=(2, 8))
        self._mudou(self.seg.get())
        self.lbl_erro = rotulo(self, "", tam=12, text_color=Cores.ALERTA, wraplength=400)
        self.lbl_erro.pack(fill="x", padx=20)
        botao(self, "Salvar", self._salvar).pack(fill="x", padx=20, pady=(6, 4))
        botao(self, "Cancelar", self.destroy, primario=False).pack(fill="x", padx=20, pady=(0, 16))
        self.transient(master)
        self.after(150, self._focar)

    def _focar(self) -> None:
        try:
            self.grab_set()
        except Exception:  # noqa: BLE001
            pass

    def _mudou(self, valor: str) -> None:
        self.menu_div.configure(state="normal" if valor.endswith("2") else "disabled")

    def _salvar(self) -> None:
        nome, cargo = self.e_nome.get().strip(), self.e_cargo.get().strip()
        nivel = int(self.seg.get()[-1])
        if len(nome) < 3 or len(cargo) < 2:
            self.lbl_erro.configure(text="Preencha nome e cargo.")
            return
        divisao = self.var_div.get() if nivel == 2 else (DIVISAO_MINISTRO if nivel == 3 else DIVISAO_GERAL)
        try:
            self.ao_salvar(self.usuario, nome, cargo, nivel, divisao)
            self.destroy()
        except ErroBanco as exc:
            self.lbl_erro.configure(text=str(exc))


class TabAdmin(TabBase):
    """Painel administrativo."""

    def __init__(self, master: Any, app: Any) -> None:
        super().__init__(master, app)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        esq = cartao(self)
        esq.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        esq.grid_columnconfigure(0, weight=1)
        esq.grid_rowconfigure(1, weight=1)
        rotulo(esq, "Usuários cadastrados", tam=16, negrito=True).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 6))
        colunas = [("nome", "Nome", 180), ("cargo", "Cargo", 170), ("nivel", "Nível", 55), ("div", "Divisão", 160),
                   ("criado", "Cadastro", 130)]
        self.tree = ttk.Treeview(esq, columns=[c[0] for c in colunas], show="headings", selectmode="browse")
        for k, t, w in colunas:
            self.tree.heading(k, text=t)
            self.tree.column(k, width=w, anchor="center" if k == "nivel" else "w")
        self.tree.grid(row=1, column=0, sticky="nsew", padx=(16, 0), pady=4)
        sb = ttk.Scrollbar(esq, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.grid(row=1, column=1, sticky="ns", pady=4, padx=(0, 8))
        barra = ctk.CTkFrame(esq, fg_color="transparent")
        barra.grid(row=2, column=0, sticky="w", padx=16, pady=12)
        botao(barra, "Editar selecionado", self._editar, width=160).pack(side="left", padx=(0, 8))
        botao(barra, "Remover selecionado", self._remover, primario=False, width=160).pack(side="left")
        self.lbl_msg = rotulo(esq, "", secundario=True, tam=12, wraplength=560)
        self.lbl_msg.grid(row=3, column=0, sticky="w", padx=16, pady=(0, 12))

        dir_ = ctk.CTkFrame(self, fg_color="transparent", width=400)
        dir_.grid(row=0, column=1, sticky="ns")
        aj = cartao(dir_)
        aj.pack(fill="x", pady=(0, 10))
        rotulo(aj, "Ajustes do reconhecimento", tam=16, negrito=True).pack(fill="x", padx=16, pady=(14, 6))
        self.lbl_tol = rotulo(aj, "", tam=13)
        self.lbl_tol.pack(fill="x", padx=16)
        self.sld_tol = ctk.CTkSlider(aj, from_=0.30, to=0.70, number_of_steps=40, command=self._mudou_tol,
                                     progress_color=Cores.ROXO_BRILHO, button_color=Cores.ROXO_BRILHO,
                                     button_hover_color=Cores.ROXO_HOVER, fg_color=Cores.BORDA)
        self.sld_tol.pack(fill="x", padx=16, pady=(2, 2))
        rotulo(aj, "Menor = mais rígido (menos falsos positivos). Padrão: 0,50.", secundario=True, tam=11).pack(
            fill="x", padx=16, pady=(0, 8))
        rotulo(aj, "Detector de rosto").pack(fill="x", padx=16)
        self.var_modelo = ctk.StringVar(value="hog")
        menu(aj, ["hog", "cnn"], self.var_modelo).pack(fill="x", padx=16, pady=(2, 8))
        rotulo(aj, "Filtro de ruído").pack(fill="x", padx=16)
        self.var_filtro = ctk.StringVar(value="bilateral")
        menu(aj, ["bilateral", "gaussiano"], self.var_filtro).pack(fill="x", padx=16, pady=(2, 8))
        self.lbl_est = rotulo(aj, "", tam=13)
        self.lbl_est.pack(fill="x", padx=16)
        self.sld_est = ctk.CTkSlider(aj, from_=1.0, to=5.0, number_of_steps=8, command=self._mudou_est,
                                     progress_color=Cores.ROXO_BRILHO, button_color=Cores.ROXO_BRILHO,
                                     button_hover_color=Cores.ROXO_HOVER, fg_color=Cores.BORDA)
        self.sld_est.pack(fill="x", padx=16, pady=(2, 8))
        botao(aj, "Salvar ajustes", self._salvar_ajustes).pack(fill="x", padx=16, pady=(4, 16))

        bk = cartao(dir_)
        bk.pack(fill="x")
        rotulo(bk, "Backup e restauração", tam=16, negrito=True).pack(fill="x", padx=16, pady=(14, 4))
        rotulo(bk, "O backup (.zip) contém o banco E a chave de criptografia. Guarde-o em local seguro.",
               secundario=True, tam=11, wraplength=360).pack(fill="x", padx=16, pady=(0, 8))
        botao(bk, "Gerar backup...", self._backup).pack(fill="x", padx=16, pady=(0, 6))
        botao(bk, "Restaurar backup...", self._restaurar, primario=False).pack(fill="x", padx=16, pady=(0, 16))

        self.bloq = ctk.CTkFrame(self, fg_color=Cores.PAINEL, corner_radius=14, border_width=1,
                                 border_color=Cores.BORDA)
        rotulo(self.bloq, "🔒  Painel administrativo", tam=22, negrito=True, anchor="center", justify="center").place(
            relx=0.5, rely=0.42, anchor="center")
        rotulo(self.bloq, "Acesso exclusivo do Ministro (nível 3).\nAutentique-se solicitando o Nível 3.",
               secundario=True, tam=14, anchor="center", justify="center").place(relx=0.5, rely=0.52, anchor="center")

    # ------------------------------------------------------------------ ciclo
    def ativar(self) -> None:
        if not self.app.sessao_valida(3):
            self.bloq.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.bloq.lift()
            return
        self.bloq.place_forget()
        a = self.app.ajustes
        self.sld_tol.set(a.tolerancia)
        self.sld_est.set(a.tempo_estavel)
        self.var_modelo.set(a.modelo_deteccao)
        self.var_filtro.set(a.filtro)
        self._mudou_tol(a.tolerancia)
        self._mudou_est(a.tempo_estavel)
        self._listar()

    def _mudou_tol(self, v: float) -> None:
        self.lbl_tol.configure(text=f"Tolerância (distância máxima): {float(v):.2f}")

    def _mudou_est(self, v: float) -> None:
        self.lbl_est.configure(text=f"Tempo de rosto estável: {float(v):.1f} s")

    def _exigir_ministro(self) -> bool:
        if self.app.sessao_valida(3):
            return True
        messagebox.showerror("Acesso negado", "Sessão de Ministro ausente ou expirada.")
        self.ativar()
        return False

    def _log(self, motivo: str) -> None:
        s = self.app.sessao
        self.app.banco.registrar_log(s.usuario.id if s else None, s.usuario.nome if s else "sistema", "ADMIN", 3, None, motivo)

    # --------------------------------------------------------------- usuários
    def _listar(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for u in self.app.banco.listar_usuarios():
            self.tree.insert("", "end", iid=str(u.id), values=(u.nome, u.cargo, u.nivel, u.divisao or "—", u.criado_em))

    def _selecionado(self) -> Optional[Usuario]:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Seleção", "Selecione um usuário na lista.")
            return None
        return self.app.banco.obter_usuario(int(sel[0]))

    def _editar(self) -> None:
        if not self._exigir_ministro():
            return
        u = self._selecionado()
        if u is not None:
            DialogoEdicao(self.winfo_toplevel(), u, self._aplicar_edicao)

    def _aplicar_edicao(self, u: Usuario, nome: str, cargo: str, nivel: int, divisao: str) -> None:
        self.app.banco.atualizar_usuario(u.id, nome, cargo, nivel, divisao)
        self._log(f"Usuário '{u.nome}' editado → '{nome}', nível {nivel}, {divisao}.")
        self.app.recarregar_classificador()
        self._listar()
        self.lbl_msg.configure(text=f"Usuário '{nome}' atualizado.")

    def _remover(self) -> None:
        if not self._exigir_ministro():
            return
        u = self._selecionado()
        if u is None:
            return
        if not messagebox.askyesno("Remover usuário", f"Remover '{u.nome}'?\nO vetor facial e a foto serão apagados. "
                                                      "O log de auditoria é mantido."):
            return
        try:
            self.app.banco.remover_usuario(u.id)
        except ErroBanco as exc:
            messagebox.showerror("Não foi possível remover", str(exc))
            return
        self._log(f"Usuário '{u.nome}' removido.")
        self.app.recarregar_classificador()
        self._listar()
        self.lbl_msg.configure(text=f"Usuário '{u.nome}' removido.")

    # ----------------------------------------------------------------- ajustes
    def _salvar_ajustes(self) -> None:
        if not self._exigir_ministro():
            return
        a = self.app.ajustes
        novo = Ajustes(tolerancia=round(float(self.sld_tol.get()), 2), modelo_deteccao=self.var_modelo.get(),
                       filtro=self.var_filtro.get(), tempo_estavel=round(float(self.sld_est.get()), 1),
                       luz_min=a.luz_min, luz_max=a.luz_max, nitidez_min=a.nitidez_min)
        self.app.banco.salvar_ajustes(novo)
        self.app.recarregar_ajustes()
        self._log(f"Ajustes: tolerância {novo.tolerancia}, detector {novo.modelo_deteccao}, filtro {novo.filtro}, "
                  f"estabilidade {novo.tempo_estavel}s.")
        self.lbl_msg.configure(text="Ajustes salvos.")

    # ------------------------------------------------------------------ backup
    def _backup(self) -> None:
        if not self._exigir_ministro():
            return
        destino = filedialog.asksaveasfilename(defaultextension=".zip", filetypes=[("Backup COFRE VERDE", "*.zip")],
                                               initialdir=str(BACKUP_DIR),
                                               initialfile=f"backup_cofre_{datetime.now():%Y%m%d_%H%M}.zip")
        if not destino:
            return
        try:
            self.app.banco.exportar_backup(Path(destino))
            self._log(f"Backup gerado: {Path(destino).name}.")
            messagebox.showinfo("Backup", "Backup gerado com sucesso.\nGuarde o arquivo em local seguro: ele inclui a chave.")
        except ErroBanco as exc:
            messagebox.showerror("Backup", str(exc))

    def _restaurar(self) -> None:
        if not self._exigir_ministro():
            return
        origem = filedialog.askopenfilename(filetypes=[("Backup COFRE VERDE", "*.zip")], initialdir=str(BACKUP_DIR))
        if not origem:
            return
        if not messagebox.askyesno("Restaurar backup", "Isto SUBSTITUI todos os dados atuais (usuários, logs e chave).\n"
                                                       "Uma cópia do estado atual será guardada em 'backups/'.\nContinuar?"):
            return
        try:
            self.app.banco.restaurar_backup(Path(origem))
        except ErroBanco as exc:
            messagebox.showerror("Restauração", str(exc))
            return
        self.app.recarregar_ajustes()
        self.app.recarregar_classificador()
        self.app.sessao = None
        self.app._atualizar_cabecalho()
        self.app.banco.registrar_log(None, "sistema", "ADMIN", 3, None, f"Backup restaurado: {Path(origem).name}.")
        messagebox.showinfo("Restauração", "Backup restaurado. Autentique-se novamente.")
        self.app.ir_para("Autenticação")


# ============================================================================
# MÓDULO: gui.app
# ============================================================================
import logging
import time
from dataclasses import dataclass
from tkinter import messagebox
from typing import Optional

import customtkinter as ctk


log = logging.getLogger(__name__)

ABAS = ["Autenticação", "Cadastro", "Pipeline", "Cofre", "Logs", "Relatórios", "Admin"]
MODO_POR_ABA = {"Autenticação": "auth", "Cadastro": "cadastro", "Pipeline": "pipeline"}
ABAS_PROTEGIDAS = {"Cofre", "Admin"}


@dataclass
class Sessao:
    """Sessão do usuário autenticado. nivel_efetivo = nível solicitado (privilégio mínimo)."""

    usuario: Usuario
    nivel_efetivo: int
    ultima_atividade: float


class AplicacaoCofre(ctk.CTk):
    """Aplicação principal COFRE VERDE."""

    def __init__(self, banco: Banco, cripto: Cripto, modelos_ok: bool, msg_modelos: str) -> None:
        super().__init__()
        aplicar_tema(self)
        self.title(f"{APP_NOME} — {APP_SUBTITULO}")
        self.geometry("1360x860")
        self.minsize(1200, 780)
        self.banco, self.cripto = banco, cripto
        self.modelos_ok, self.msg_modelos = modelos_ok, msg_modelos
        self.sessao: Optional[Sessao] = None
        self._ajustes: Ajustes = banco.carregar_ajustes()
        self._aba_atual = ""

        self.classificador = Classificador()
        self.recarregar_classificador()
        self.camera = Camera()
        self.bloqueio = ControleBloqueio(banco)
        self.decisor = Decisor(self.classificador, lambda: self._ajustes, self.bloqueio)
        self.loop = LoopReconhecimento(self.camera, self.classificador, lambda: self._ajustes, self.decisor)
        self.loop.modelos_ok, self.loop.msg_modelos = modelos_ok, msg_modelos

        self._montar_cabecalho()
        self._montar_abas()
        self.loop.start()
        self.after(200, self.reiniciar_camera)
        self.after(500, self._tick_geral)
        self.bind_all("<Button-1>", lambda e: self._atividade(), add="+")
        self.bind_all("<Key>", lambda e: self._atividade(), add="+")
        self.protocol("WM_DELETE_WINDOW", self.encerrar)

        if banco.aviso_recuperacao:
            self.after(600, lambda: messagebox.showwarning("Banco de dados recuperado", banco.aviso_recuperacao))
        if not modelos_ok:
            self.after(800, lambda: messagebox.showerror("Modelos não carregados", msg_modelos))
        if not banco.existe_ministro():
            self.after(1000, self._primeiro_uso)

    # ------------------------------------------------------------------ layout
    def _montar_cabecalho(self) -> None:
        topo = ctk.CTkFrame(self, fg_color=Cores.PAINEL, corner_radius=0, height=64, border_width=0)
        topo.pack(fill="x")
        topo.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(topo, text="◆", text_color=Cores.ROXO_BRILHO, font=fonte(26, True)).grid(
            row=0, column=0, rowspan=2, padx=(20, 8), pady=8)
        rotulo(topo, APP_NOME, tam=20, negrito=True).grid(row=0, column=1, sticky="sw", pady=(8, 0))
        rotulo(topo, f"{APP_SUBTITULO} · Ministério do Meio Ambiente", secundario=True, tam=11).grid(
            row=1, column=1, sticky="nw", pady=(0, 8))
        self.lbl_camera = rotulo(topo, "● Câmera: iniciando", secundario=True, tam=12, anchor="e")
        self.lbl_camera.grid(row=0, column=2, rowspan=2, padx=12)
        self.lbl_sessao = rotulo(topo, "Sem sessão ativa", secundario=True, tam=12, negrito=True, anchor="e")
        self.lbl_sessao.grid(row=0, column=3, rowspan=2, padx=12)
        self.btn_encerrar = botao(topo, "Encerrar sessão", lambda: self.encerrar_sessao("Sessão encerrada."),
                                  primario=False, width=130, state="disabled")
        self.btn_encerrar.grid(row=0, column=4, rowspan=2, padx=(0, 20))
        ctk.CTkFrame(self, fg_color=Cores.ROXO, height=2, corner_radius=0).pack(fill="x")

    def _montar_abas(self) -> None:
        self.abas = ctk.CTkTabview(
            self, fg_color="transparent", command=self._ao_trocar_aba,
            segmented_button_fg_color=Cores.PAINEL, segmented_button_selected_color=Cores.ROXO,
            segmented_button_selected_hover_color=Cores.ROXO_HOVER,
            segmented_button_unselected_color=Cores.PAINEL, segmented_button_unselected_hover_color=Cores.BORDA,
            text_color=Cores.TEXTO, corner_radius=12)
        self.abas.pack(fill="both", expand=True, padx=16, pady=(8, 14))
        self.abas._segmented_button.configure(font=fonte(13, True))
        classes = {"Autenticação": TabAutenticacao, "Cadastro": TabCadastro, "Pipeline": TabPipeline,
                   "Cofre": TabCofre, "Logs": TabLogs, "Relatórios": TabRelatorios, "Admin": TabAdmin}
        self.tabs: dict[str, TabBase] = {}
        for nome in ABAS:
            quadro = self.abas.add(nome)
            quadro.grid_rowconfigure(0, weight=1)
            quadro.grid_columnconfigure(0, weight=1)
            tab = classes[nome](quadro, self)
            tab.grid(row=0, column=0, sticky="nsew")
            self.tabs[nome] = tab
        self.after(100, lambda: self.ir_para("Autenticação"))

    # -------------------------------------------------------------------- abas
    def ir_para(self, nome: str) -> None:
        """Seleciona a aba (programaticamente) e dispara ativar()/desativar()."""
        self.abas.set(nome)
        self._ao_trocar_aba()

    def _ao_trocar_aba(self) -> None:
        nome = self.abas.get()
        if nome == self._aba_atual:
            return
        if self._aba_atual in self.tabs:
            self.tabs[self._aba_atual].desativar()
        self._aba_atual = nome
        self.loop.definir_modo(MODO_POR_ABA.get(nome, "idle"))
        self.tabs[nome].ativar()

    def _primeiro_uso(self) -> None:
        messagebox.showinfo("Primeiro uso", "Nenhum usuário cadastrado ainda.\n\nCadastre primeiro o Ministro "
                            "(nível 3) na aba Cadastro. Depois ele poderá cadastrar os demais usuários.")
        self.ir_para("Cadastro")

    # ------------------------------------------------------------------ serviços
    @property
    def ajustes(self) -> Ajustes:
        return self._ajustes

    def recarregar_ajustes(self) -> None:
        """Relê os ajustes do banco (após o admin salvar)."""
        self._ajustes = self.banco.carregar_ajustes()

    def recarregar_classificador(self) -> None:
        """Recarrega os encodings decifrados na memória (após cadastro/edição/remoção/restauração)."""
        try:
            self.classificador.recarregar(self.banco.listar_usuarios(com_encoding=True))
        except Exception as exc:  # noqa: BLE001
            log.exception("Falha ao carregar base de rostos")
            messagebox.showerror("Erro", f"Não foi possível carregar a base de rostos: {exc}")

    def reiniciar_camera(self) -> None:
        """(Re)abre a câmera em segundo plano, sem travar a interface."""
        self.lbl_camera.configure(text="● Câmera: iniciando...", text_color=Cores.TEXTO_SEC)
        executar_em_thread(self, self.camera.iniciar, lambda r: None, lambda e: log.error("Câmera: %s", e))

    # ------------------------------------------------------------------- sessão
    def definir_sessao(self, usuario: Usuario, nivel_efetivo: int) -> None:
        self.sessao = Sessao(usuario, nivel_efetivo, time.time())
        self._atualizar_cabecalho()

    def sessao_valida(self, nivel_min: int = 1) -> bool:
        s = self.sessao
        return s is not None and s.nivel_efetivo >= nivel_min and (time.time() - s.ultima_atividade) < SESSAO_TIMEOUT_S

    def encerrar_sessao(self, motivo: str = "") -> None:
        if self.sessao is not None:
            self.banco.registrar_log(self.sessao.usuario.id, self.sessao.usuario.nome, "SESSAO", self.sessao.nivel_efetivo,
                                     None, motivo or "Sessão encerrada.")
        self.sessao = None
        self._atualizar_cabecalho()
        if self.abas.get() in ABAS_PROTEGIDAS | {"Cadastro"}:
            self.ir_para("Autenticação")
        else:
            self.tabs[self.abas.get()].ativar()

    def _atividade(self) -> None:
        if self.sessao is not None:
            self.sessao.ultima_atividade = time.time()

    def _atualizar_cabecalho(self) -> None:
        s = self.sessao
        if s is None:
            self.lbl_sessao.configure(text="Sem sessão ativa", text_color=Cores.TEXTO_SEC)
            self.btn_encerrar.configure(state="disabled")
        else:
            self.lbl_sessao.configure(text=f"● {s.usuario.nome} · Nível {s.nivel_efetivo}", text_color=Cores.ROXO_BRILHO)
            self.btn_encerrar.configure(state="normal")

    def _tick_geral(self) -> None:
        cam = self.camera
        if cam.ativa:
            self.lbl_camera.configure(text=f"● Câmera: ativa · {cam.fps:.0f} fps", text_color=Cores.ROXO_BRILHO)
        else:
            self.lbl_camera.configure(text="● Câmera: indisponível", text_color=Cores.ALERTA)
        if self.sessao is not None and not self.sessao_valida():
            self.encerrar_sessao("Sessão expirada por inatividade.")
            messagebox.showinfo("Sessão expirada", "Sua sessão expirou por inatividade. Autentique-se novamente.")
        self.after(1000, self._tick_geral)

    def encerrar(self) -> None:
        """Libera câmera, thread e banco antes de fechar."""
        try:
            self.loop.parar()
            self.camera.parar()
            self.banco.fechar()
        finally:
            self.destroy()


# ============================================================================
# MÓDULO: main
# ============================================================================
import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler



def configurar_logging() -> None:
    """Log técnico em logs/cofre_verde.log (separado da auditoria, que fica no SQLite)."""
    handler = RotatingFileHandler(LOGS_DIR / "cofre_verde.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()])


def abrir_banco():
    """Abre chave e banco; devolve (banco, cripto)."""
    cripto = Cripto(CHAVE_PATH)
    return Banco(DB_PATH, cripto), cripto


def redefinir_ministro() -> int:
    """Recuperação com ACESSO FÍSICO ao computador: remove o usuário nível 3 para recadastrar outro."""
    banco, _ = abrir_banco()
    print("ATENÇÃO: isto removerá o Ministro (nível 3) atual. Os demais usuários e logs são mantidos.")
    if input("Digite CONFIRMAR para continuar: ").strip() != "CONFIRMAR":
        print("Cancelado.")
        return 1
    n = banco.remover_ministro()
    banco.registrar_log(None, "sistema", "ADMIN", 3, None, f"Ministro redefinido via linha de comando ({n} removido).")
    print(f"{n} usuário(s) nível 3 removido(s). Abra o programa: a aba Cadastro permitirá cadastrar o novo Ministro.")
    return 0


def main() -> int:
    configurar_logging()
    parser = argparse.ArgumentParser(description="COFRE VERDE — Autenticação Biométrica Facial")
    parser.add_argument("--redefinir-ministro", action="store_true", help="remove o usuário nível 3 (acesso físico)")
    args = parser.parse_args()
    if args.redefinir_ministro:
        return redefinir_ministro()

    try:
        import customtkinter  # noqa: F401
    except ImportError:
        print("CustomTkinter não instalado. Execute: pip install -r requirements.txt")
        return 2
    from tkinter import messagebox

    try:
        banco, cripto = abrir_banco()
    except Exception as exc:  # noqa: BLE001
        logging.exception("Falha ao iniciar banco/criptografia")
        import tkinter
        raiz = tkinter.Tk()
        raiz.withdraw()
        messagebox.showerror("COFRE VERDE", f"Não foi possível iniciar o banco de dados ou a chave de criptografia:\n{exc}")
        return 1

    modelos_ok, msg = verificar_modelos()
    if not modelos_ok:
        logging.error(msg)
    app = AplicacaoCofre(banco, cripto, modelos_ok, msg)
    app.mainloop()
    return 0





# ============================================================================
# EXECUÇÃO
# ============================================================================
if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException:  # noqa: BLE001
        import traceback
        erro = traceback.format_exc()
        print("\n[ERRO FATAL]\n" + erro)
        try:
            (LOGS_DIR / "erro_fatal.log").write_text(erro, encoding="utf-8")
        except OSError:
            pass
        _pausar()
        sys.exit(1)
