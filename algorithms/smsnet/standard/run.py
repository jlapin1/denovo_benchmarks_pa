# Copyright 2019 Korrawe Karunratanakul
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

from __future__ import print_function

import argparse
import os
import random
import sys

import numpy as np
import tensorflow as tf

from nmt import inference
from nmt import train
from nmt.utils import evaluation_utils
from nmt.utils import misc_utils as utils
from nmt.utils import vocab_utils
from nmt.utils import iterator_utils
from utils_data import convert_mgf_to_csv as mgf2csv

utils.check_tensorflow_version()

FLAGS = None

from nmt import model as nmt_model
from nmt import model_helper
from nmt import post_process

input_files = []
for i in range(22):
    input_files.append("data_best_compat/train_" + str(i))

hparams = tf.contrib.training.HParams(
      # Data
      src=input_files,
      tgt='_tgt.csv',
      #train_prefix=flags.train_prefix,
      #dev_prefix=flags.dev_prefix,
      #test_prefix=flags.test_prefix,
      #vocab_prefix=flags.vocab_prefix,
      #embed_prefix=flags.embed_prefix,
      out_dir="log_ablation/log_best_700k",# encoder_to_decoder_lookahead",
      tgt_vocab_file="nmt/vocab/vocab_m.txt",
      tgt_vocab_size=24,
      tgt_embed_file="",
      
      dev="data_best_compat/val_no_dup",
      test="data_best_compat/test_no_dup",
      src_suffix=".csv",
      train_fin=True,

      # Networks
      num_units=512,
      num_layers=2,  # Compatible
      #num_encoder_layers=1, #(flags.num_encoder_layers or flags.num_layers),
      num_decoder_layers=2 ,#(flags.num_decoder_layers or flags.num_layers),
      dropout=0.1,
      unit_type="layer_norm_lstm", 
      residual=True,
      num_decoder_residual_layers = 1,
      time_major=True,
      num_embeddings_partitions=0,

      # Attention mechanisms
      attention="",
      #attention_architecture=flags.attention_architecture,
      #output_attention=flags.output_attention,
      #pass_hidden_state=flags.pass_hidden_state,

      # Train
      optimizer="sgd",
      num_train_steps=700000,
      batch_size=32,
      init_op="uniform",
      init_weight=0.1,
      max_gradient_norm=5.0,
      learning_rate=0.01,
      warmup_steps=0,
      warmup_scheme="t2t",
      decay_scheme="luong234",
      colocate_gradients_with_ops=True,

      # Data constraints
      num_buckets=2,
      max_train=0,
      src_max_len=None,
      tgt_max_len=None,

      # Inference
      src_max_len_infer=None,
      tgt_max_len_infer=50,
      infer_batch_size=8,
      beam_width=20,
      length_penalty_weight=1.0,
      sampling_temperature=0.0,
      num_translations_per_input=1,

      # Vocab
      sos='<s>', #flags.sos if flags.sos else vocab_utils.SOS,
      eos='</s>', #flags.eos if flags.eos else vocab_utils.EOS,
      subword_option=None,
      check_special_token=None,
      embed_size=32,

      # Misc
      forget_bias=1.0,
      num_gpus=1,
      epoch_step=0, # record where we were within an epoch.
      steps_per_stats=200,
      steps_per_external_eval=20000,
      share_vocab=None,
      metrics=["bleu","accuracy","amino_acid_accuracy"], # rouge
      log_device_placement=False,
      random_seed=48,
      override_loaded_hparams=False,
      num_keep_ckpts=5,
      avg_ckpts=False,
      num_intra_threads=None,# flags.num_intra_threads,
      num_inter_threads=None
  )

# Evaluation
for metric in hparams.metrics:
  hparams.add_hparam("best_" + metric, 0)  # larger is better
  best_metric_dir = os.path.join(hparams.out_dir, "best_" + metric)
  hparams.add_hparam("best_" + metric + "_dir", best_metric_dir)
  tf.gfile.MakeDirs(best_metric_dir)

  if hparams.avg_ckpts:
    hparams.add_hparam("avg_best_" + metric, 0)  # larger is better
    best_metric_dir = os.path.join(hparams.out_dir, "avg_best_" + metric)
    hparams.add_hparam("avg_best_" + metric + "_dir", best_metric_dir)
    tf.gfile.MakeDirs(best_metric_dir)


if __name__ == "__main__":
  parser = argparse.ArgumentParser()
  parser.register("type", "bool", lambda v: v.lower() == "true")
  parser.add_argument("--inference_input_file", type=str, default=None,
                      help="Set to the text to decode.")
  parser.add_argument("--ckpt", type=str, default="",
                      help="Checkpoint file to load a model for inference.")
  parser.add_argument("--model_dir", type=str, default="",
                      help="Directory to load a model for inference.")
  parser.add_argument("--rescore", type="bool", nargs="?", const=True,
                      default=False,
                      help="Rescore with previously trained model.")
  parser.add_argument("--rescore_logdir", type=str, default=None,
                      help="Directory to save or load model for rescoring.")
  args = parser.parse_args()
  print(args)
  if args.inference_input_file:
    infer_input_file = args.inference_input_file
    # Inference
    hparams.inference_indices = None
    print(infer_input_file)

    source_filename = os.path.basename(infer_input_file)[:-4] # no ".mgf"
    input_dir = os.path.dirname(infer_input_file)

    trans_dir = input_dir + '_output/'
    trans_file = os.path.join(trans_dir, source_filename)

    if not os.path.exists(trans_dir):
      pass # os.mkdir(trans_dir)

    # convert to csv format if nessesary to speed-up inference
    if infer_input_file[-3:] == 'mgf':
      mgf2csv.main([trans_dir, infer_input_file])
      infer_input_file = os.path.join(trans_dir, source_filename + '.csv')
      del_temp_file = True
    else:
      del_temp_file = False


    # check model path
    ckpt = args.ckpt
    if not ckpt:
      model_dir = hparams.out_dir
    if args.model_dir:
      model_dir = args.model_dir
    ckpt = tf.train.latest_checkpoint(model_dir)
    
    # decode
    inference.inference(ckpt, infer_input_file, trans_file, hparams)

    if args.rescore:
      if not args.rescore_logdir:
        rescore_dir = os.path.join(model_dir, "post_process")
      post_process.rescore(trans_file, trans_file + "_prob", infer_input_file,
                           rescore_dir, trans_dir + source_filename + "_rescore")
      print("Done")
    
    if del_temp_file:
      os.remove(infer_input_file)

  else:
    print('training')
    train.train(hparams)