"""Synthetic-only checks; never reads an actual learner dataset."""
import hashlib
import json
from pathlib import Path
import numpy as np
from sklearn.model_selection import GroupKFold

groups=np.repeat(np.array([f"synthetic-group-{n}" for n in range(10)]),range(1,11))
partitions=[]
for repeat in range(5):
    order=np.random.default_rng(42+repeat).permutation(len(groups))
    shuffled=groups[order]
    partitions.append(tuple(tuple(sorted(set(shuffled[test]))) for train,test in GroupKFold(n_splits=5).split(np.zeros((len(groups),1)),groups=shuffled)))

def published_key(name,school):
    raw=f"smartpace::{school.strip().upper()}::{name.strip().upper()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

target=published_key("Invented Example Learner","Invented School")
candidate_names=["Another Invented Learner","Invented Example Learner"]
matches=[name for name in candidate_names if published_key(name,"Invented School")==target]
result={"synthetic_repeats":len(partitions),"distinct_partitionings":len(set(partitions)),"all_repeat_folds_identical":len(set(partitions))==1,"synthetic_name_dictionary_match_succeeds":matches==["Invented Example Learner"],"real_dataset_values_read":False}
(Path(__file__).parent/"ml-synthetic-checks.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
print(json.dumps(result,indent=2))
