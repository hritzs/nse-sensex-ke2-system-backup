"""Explicit service ownership and independent writable settings per EXE."""
from pathlib import Path
PROFILES={
 "all":{"folder":"WhiteGammaStandalone","control":18201,
        "services":("feed","whitegamma","gamma","tester","indicative"),"title":"WhiteGamma complete system"},
 "feed":{"folder":"WhiteGammaKE2Feed","control":18301,
         "services":("feed","tester","indicative"),"title":"KE2 feed and decoder"},
 "gamma":{"folder":"WhiteGammaApplication","control":18201,
          "services":("whitegamma","gamma"),"title":"WhiteGamma and Gamma simulator"},
 "whitegamma":{"folder":"WhiteGammaDesktop","control":18201,
          "services":("whitegamma",),"title":"WhiteGamma application"},
 "simulator":{"folder":"GammaSimulator","control":18401,
          "services":("gamma",),"title":"Gamma simulator"},
}
PORT_KEYS={"feed":{"raw","publish","routed","routed_raw"},"tester":{"tester"},
           "indicative":{"indicative"},"whitegamma":{"whitegamma"},"gamma":{"gamma"}}
def executable_profile(path):
    stem=Path(path).stem.lower()
    return {"ke2feed":"feed","whitegamma":"whitegamma","whitegammaapp":"whitegamma",
            "gammasimulator":"simulator"}.get(stem,"all")
def owned_ports(profile,ports):
    keys=set().union(*(PORT_KEYS[s] for s in PROFILES[profile]["services"]))
    return {k:v for k,v in ports.items() if k in keys}
