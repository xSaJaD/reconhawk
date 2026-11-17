import unittest
from unittest.mock import patch
from app.config import settings
from app.scope import check_url, ScopeError


class ScopeURLTests(unittest.TestCase):
    def setUp(self):
        self.enabled = settings.scope_enforced
        settings.scope_enforced=True
        self.private=patch('app.scope._is_private', return_value=False)
        self.private.start()
    def tearDown(self):
        self.private.stop();settings.scope_enforced=self.enabled

    def test_exact_path_does_not_authorize_other_handlers(self):
        allow=['https://app.example.test/api/owned']
        check_url(allow[0],allow,[])
        for url in ['https://app.example.test/api/admin','https://app.example.test/api/owned-extra']:
            with self.assertRaises(ScopeError):check_url(url,allow,[])

    def test_subtree_and_glob_are_explicit(self):
        for rule in ['https://app.example.test/api/', 'https://app.example.test/api/*']:
            check_url('https://app.example.test/api/owned', [rule], [])
            with self.assertRaises(ScopeError):check_url('https://app.example.test/api-other/owned',[rule],[])

    def test_url_scheme_and_port_are_preserved(self):
        allow=['https://app.example.test:8443/api/']
        check_url('https://app.example.test:8443/api/owned',allow,[])
        for url in ['https://app.example.test/api/owned','http://app.example.test:8443/api/owned']:
            with self.assertRaises(ScopeError):check_url(url,allow,[])

    def test_plain_host_rules_keep_host_semantics(self):
        check_url('https://app.example.test:8443/any',['*.example.test'],[])

    def test_path_exclusion_and_ambiguous_encoding_cannot_escape(self):
        allow=['app.example.test'];deny=['https://app.example.test/private/']
        check_url('https://app.example.test/public',allow,deny)
        for path in ['/private/owned','/private%2fowned','/%70rivate%252fowned','/private\\owned','/private;escape/owned']:
            with self.assertRaises(ScopeError):check_url('https://app.example.test'+path,allow,deny)

    def test_query_restriction_does_not_expand(self):
        rule='https://app.example.test/api?tenant=owned'
        check_url(rule,[rule],[])
        with self.assertRaises(ScopeError):check_url('https://app.example.test/api?tenant=other',[rule],[])
