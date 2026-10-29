import unittest
from standalone.profiles import PROFILES,owned_ports,executable_profile
class ProfileTests(unittest.TestCase):
    def test_feed_never_owns_simulation_or_application(self):
        self.assertEqual(PROFILES["feed"]["services"],("feed","tester","indicative"))
        ports={key:n+10000 for n,key in enumerate(("raw","publish","tester","routed","routed_raw","gamma","indicative","whitegamma","control"))}
        self.assertNotIn("gamma",owned_ports("feed",ports))
        self.assertNotIn("publish",owned_ports("gamma",ports))
    def test_independent_data_and_control_ports(self):
        self.assertNotEqual(PROFILES["feed"]["folder"],PROFILES["gamma"]["folder"])
        self.assertNotEqual(PROFILES["feed"]["control"],PROFILES["gamma"]["control"])
    def test_three_requested_products_do_not_share_owned_ports(self):
        ports={key:n+10000 for n,key in enumerate(("raw","publish","tester","routed","routed_raw","gamma","indicative","whitegamma","control"))}
        groups=[set(owned_ports(p,ports)) for p in ("feed","whitegamma","simulator")]
        self.assertFalse(groups[0]&groups[1]);self.assertFalse(groups[0]&groups[2]);self.assertFalse(groups[1]&groups[2])
        self.assertEqual(len({PROFILES[p]["folder"] for p in ("feed","whitegamma","simulator")}),3)
        self.assertEqual(len({PROFILES[p]["control"] for p in ("feed","whitegamma","simulator")}),3)
    def test_executable_names_select_profiles(self):
        self.assertEqual(executable_profile("KE2Feed.exe"),"feed")
        self.assertEqual(executable_profile("WhiteGamma.exe"),"whitegamma")
        self.assertEqual(executable_profile("GammaSimulator.exe"),"simulator")
if __name__=="__main__":unittest.main()
