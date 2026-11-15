"""The agent is told to provision two accounts when (and only when) the operator enabled it."""
import unittest
from app.agent.prompts import auth_testing_directive, SYSTEM_PROMPT


class AuthDirectiveTests(unittest.TestCase):
    def test_enabled_directs_provisioning_two_accounts(self):
        d = auth_testing_directive(True, "2")
        self.assertIn("AUTHENTICATED TESTING IS ENABLED", d)
        self.assertIn("register_test_account", d)
        self.assertIn("userA", d); self.assertIn("userB", d)
        self.assertIn("cap 2", d)
        # Guard restated: never bypass human verification.
        self.assertIn("never attempt to bypass", d.lower().replace("—", "-"))
        self.assertIn("CAPTCHA", d)

    def test_disabled_explains_the_limitation(self):
        d = auth_testing_directive(False)
        self.assertIn("AUTHENTICATED TESTING IS OFF", d)
        self.assertIn("IDOR/BOLA", d)
        self.assertIn("enable test-account provisioning", d)
        self.assertNotIn("register_test_account", d)  # does not tell it to provision when off

    def test_base_prompt_still_forbids_bypassing_verification(self):
        low = SYSTEM_PROMPT.lower()
        self.assertIn("captcha", low)
        self.assertIn("bypass", low)          # the prompt forbids bypassing human verification
        self.assertIn("never", low)


if __name__ == "__main__":
    unittest.main()
