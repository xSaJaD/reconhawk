"""Unittest discovery imports modules in order; isolate app globals before imports."""
import os
import tempfile

storage = tempfile.TemporaryDirectory(prefix='reconhawk-suite-')
os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///' + storage.name + '/suite.db'
os.environ['ARTIFACTS_DIR'] = storage.name + '/artifacts'
os.environ['ANTHROPIC_API_KEY'] = ''
