import os,sys

from colabdesign.mpnn import mk_mpnn_model
from colabdesign.af import mk_af_model
from colabdesign.shared.protein import pdb_to_string
from colabdesign.shared.parse_args import parse_args

import pandas as pd
import numpy as np
from string import ascii_uppercase, ascii_lowercase
alphabet_list = list(ascii_uppercase+ascii_lowercase)

def get_info(contig):
  F = []
  fixed_chain = True
  sub_contigs = [x.split("-") for x in contig.split("/")]
  for n,(a,b) in enumerate(sub_contigs):
    if a[0].isalpha():
      L = int(b)-int(a[1:]) + 1
      F += [1] * L
    else:
      L = int(b)
      F += [0] * L
      fixed_chain = False
  return F,fixed_chain

def main(argv):
  ag = parse_args()
  ag.txt("-------------------------------------------------------------------------------------")
  ag.txt("Designability Test")
  ag.txt("-------------------------------------------------------------------------------------")
  ag.txt("REQUIRED")
  ag.txt("-------------------------------------------------------------------------------------")
  ag.add(["pdb="          ],  None,   str, ["input pdb"])
  ag.add(["loc="          ],  None,   str, ["location to save results"])
  ag.add(["contigs="      ],  None,   str, ["contig definition"])
  ag.txt("-------------------------------------------------------------------------------------")
  ag.txt("OPTIONAL")
  ag.txt("-------------------------------------------------------------------------------------")
  ag.add(["copies="       ],     1,   int, ["number of repeating copies"])
  ag.add(["num_seqs="     ],     8,   int, ["number of mpnn designs to evaluate"])
  ag.add(["initial_guess" ], False,  None, ["initialize previous coordinates"])
  ag.add(["use_multimer"  ], False,  None, ["use alphafold_multimer_v3"])
  ag.add(["num_recycles=" ],     3,   int, ["number of recycles"])
  ag.add(["rm_aa="],            "C",  str, ["disable specific amino acids from being sampled"])
  ag.txt("-------------------------------------------------------------------------------------")
  o = ag.parse(argv)

  if None in [o.pdb, o.loc, o.contigs]:
    ag.usage("Missing Required Arguments")


  contigs = o.contigs.split(":")
  chains = alphabet_list[:len(contigs)]
  info = [get_info(x) for x in contigs]
  fixed_chains = [y for x,y in info]
  fixed_pos = sum([x for x,y in info],[])

  flags = {"initial_guess":o.initial_guess,
           "best_metric":"rmsd",
           "use_multimer":o.use_multimer,
           "model_names":["model_1_multimer_v3" if o.use_multimer else "model_1_ptm"]}

  if sum(fixed_chains) > 0 and sum(fixed_chains) < len(fixed_chains):
    protocol = "binder"
    print("protocol=binder")
    target_chains = []
    binder_chains = []
    for n,x in enumerate(fixed_chains):
      if x: target_chains.append(chains[n])
      else: binder_chains.append(chains[n])
    af_model = mk_af_model(protocol="binder",**flags)
    af_model.prep_inputs(o.pdb,
                         target_chain=",".join(target_chains),
                         binder_chain=",".join(binder_chains),
                         rm_aa=o.rm_aa)
  elif sum(fixed_pos) > 0:
    protocol = "partial"
    print("protocol=partial")
    af_model = mk_af_model(protocol="fixbb",
                           use_templates=True,
                           **flags)
    rm_template = np.array(fixed_pos) == 0
    af_model.prep_inputs(o.pdb,
                         chain=",".join(chains),
                         rm_template=rm_template,
                         rm_template_seq=rm_template,
                         copies=o.copies,
                         homooligomer=o.copies>1,
                         rm_aa=o.rm_aa)
    p = np.where(fixed_pos)[0]
    af_model.opt["fix_pos"] = p[p < af_model._len]

  else:
    protocol = "fixbb"
    print("protocol=fixbb")
    af_model = mk_af_model(protocol="fixbb",**flags)
    af_model.prep_inputs(o.pdb,
                         chain=",".join(chains),
                         copies=o.copies,
                         homooligomer=o.copies>1,
                         rm_aa=o.rm_aa)

  print("running proteinMPNN...")
  sampling_temp = 0.1
  mpnn_model = mk_mpnn_model()
  mpnn_model.get_af_inputs(af_model)
  out = mpnn_model.sample(num=o.num_seqs//8, batch=8, temperature=sampling_temp)


  print("running AlphaFold...")
  if protocol == "binder":
    af_terms = ["plddt","i_ptm","i_pae","rmsd"]
  elif o.copies > 1:
    af_terms = ["plddt","ptm","i_ptm","pae","i_pae","rmsd"]
  else:
    af_terms = ["plddt","ptm","pae","rmsd"]
  for k in af_terms: out[k] = []
  os.system(f"mkdir -p {o.loc}/all_pdb")
  with open(f"{o.loc}/design.fasta","w") as fasta:
    for n in range(o.num_seqs):
      seq = out["seq"][n][-af_model._len:]
      af_model.predict(seq=seq, num_recycles=o.num_recycles, verbose=False)
      for t in af_terms: out[t].append(af_model.aux["log"][t])
      if "i_pae" in out:
        out["i_pae"][-1] = out["i_pae"][-1] * 31
      if "pae" in out:
        out["pae"][-1] = out["pae"][-1] * 31
        
      af_model.save_current_pdb(f"{o.loc}/all_pdb/n{n}.pdb")
      af_model._save_results(save_best=True, verbose=False)
      af_model._k += 1
      score_line = [f'mpnn:{out["score"][n]:.3f}']
      for t in af_terms:
        score_line.append(f'{t}:{out[t][n]:.3f}')
      print(n, " ".join(score_line)+" "+seq)
      line = f'>{"|".join(score_line)}\n{seq}'
      fasta.write(line+"\n")

  af_model.save_pdb(f"{o.loc}/best.pdb")

  labels = ["score"] + af_terms + ["seq"]
  data = [[out[k][n] for k in labels] for n in range(o.num_seqs)]
  labels[0] = "mpnn"

  df = pd.DataFrame(data, columns=labels)
  df.to_csv(f'{o.loc}/mpnn_results.csv')

if __name__ == "__main__":
   main(sys.argv[1:])