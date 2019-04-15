import os
import tensorflow as tf
import re
import numpy as np
import time
from sklearn.model_selection import train_test_split

# Download and extract dataset
path_to_zip = tf.keras.utils.get_file(
    'cornell_movie_dialogs.zip',
    origin=
    'http://www.cs.cornell.edu/~cristian/data/cornell_movie_dialogs_corpus.zip',
    extract=True)

path_to_dataset = os.path.join(
    os.path.dirname(path_to_zip), "cornell movie-dialogs corpus")

path_to_movie_lines = os.path.join(path_to_dataset, 'movie_lines.txt')
path_to_movie_conversations = os.path.join(path_to_dataset,
                                           'movie_conversations.txt')


def preprocess_sentence(sentence):
  sentence = sentence.lower().strip()
  # creating a space between a word and the punctuation following it
  # eg: "he is a boy." => "he is a boy ."
  sentence = re.sub(r"([?.!,¿])", r" \1 ", sentence)
  sentence = re.sub(r'[" "]+', " ", sentence)
  # replacing everything with space except (a-z, A-Z, ".", "?", "!", ",")
  sentence = re.sub(r"[^a-zA-Z?.!,¿]+", " ", sentence)
  sentence = sentence.rstrip().strip()
  # adding a start and an end token to the sentence
  return '<start> ' + sentence + ' <end>'


def create_dataset():
  # dictionary of line id to text
  id2line = {}
  with open(path_to_movie_lines, errors='ignore') as file:
    for line in file.read().splitlines():
      parts = line.split(' +++$+++ ')
      id2line[parts[0]] = parts[4]

  inputs, targets = [], []
  with open(path_to_movie_conversations, 'r') as file:
    for line in file.read().splitlines():
      parts = line.split(' +++$+++ ')
      # get conversation in a list of line ID
      conversation = [line[1:-1] for line in parts[3][1:-1].split(', ')]
      for i in range(len(conversation) - 1):
        inputs.append(preprocess_sentence(id2line[conversation[i]]))
        targets.append(preprocess_sentence(id2line[conversation[i + 1]]))

  return inputs, targets


def tokenize(sentences):
  tokenizer = tf.keras.preprocessing.text.Tokenizer(filters='')
  tokenizer.fit_on_texts(sentences)
  tensor = tokenizer.texts_to_sequences(sentences)
  tensor = tf.keras.preprocessing.sequence.pad_sequences(tensor, padding='post')
  return tensor, tokenizer


def load_dataset():
  # creating cleaned input, output pairs
  inputs, targets = create_dataset()
  input_tensor, input_tokenizer = tokenize(inputs)
  target_tensor, target_tokenizer = tokenize(targets)
  return input_tensor, target_tensor, input_tokenizer, target_tokenizer


# load and tokenize dataset
input_tensor, target_tensor, input_tokenizer, target_tokenizer = load_dataset()

# get max_length of the inputs and target tensors
max_input_len, max_target_len = input_tensor.shape[-1], target_tensor.shape[-1]

# split into train and evaluation sets using 80-20 split
input_train, input_eval, target_train, target_eval = train_test_split(
    input_tensor, target_tensor, test_size=0.2, shuffle=True)

print('train set size: %d\ntest set size: %d' % (len(input_train),
                                                 len(input_eval)))


def convert(tokenizer, tensor):
  for t in tensor:
    if t != 0:
      print("%d ----> %s" % (t, tokenizer.index_word[t]))


print("Input; index to word mapping")
convert(input_tokenizer, input_train[0])
print()
print("Target; index to word mapping")
convert(target_tokenizer, target_train[0])

BUFFER_SIZE = len(input_train)
BATCH_SIZE = 64
steps_per_epoch = len(input_train) // BATCH_SIZE
embedding_dim = 256
units = 1024
vocab_inp_size = len(input_tokenizer.word_index) + 1
vocab_tar_size = len(target_tokenizer.word_index) + 1

# train dataset
train_ds = tf.data.Dataset.from_tensor_slices(
    (input_train, target_train)).shuffle(BUFFER_SIZE)
train_ds = train_ds.batch(BATCH_SIZE, drop_remainder=True)

# eval dataset
eval_ds = tf.data.Dataset.from_tensor_slices((input_eval,
                                              target_eval)).shuffle(BUFFER_SIZE)
eval_ds = eval_ds.batch(BATCH_SIZE, drop_remainder=True)


def get_angles(pos, i, d_model):
  angle_rates = 1 / np.power(10000, (2 * (i // 2)) / np.float32(d_model))
  return pos * angle_rates


def positional_encoding(position, d_model):
  angle_rads = get_angles(
      np.arange(position)[:, np.newaxis],
      np.arange(d_model)[np.newaxis, :], d_model)

  # apply sin to even indices in the array; 2i
  sines = np.sin(angle_rads[:, 0::2])

  # apply cos to odd indices in the array; 2i+1
  cosines = np.cos(angle_rads[:, 1::2])

  pos_encoding = np.concatenate([sines, cosines], axis=-1)

  pos_encoding = pos_encoding[np.newaxis, ...]

  return tf.cast(pos_encoding, dtype=tf.float32)


def create_padding_mask(seq):
  seq = tf.cast(tf.math.equal(seq, 0), tf.float32)

  # add extra dimensions so that we can add the padding
  # to the attention logits.
  return seq[:, tf.newaxis, tf.newaxis, :]  # (batch_size, 1, 1, seq_len)


def create_look_ahead_mask(size):
  mask = 1 - tf.linalg.band_part(tf.ones((size, size)), -1, 0)
  return mask  # (seq_len, seq_len)


def scaled_dot_product_attention(q, k, v, mask):
  """Calculate the attention weights.
  q, k, v must have matching leading dimensions.
  The mask has different shapes depending on its type(padding or look ahead) 
  but it must be broadcastable for addition.

  Args:
    q: query shape == (..., seq_len_q, depth)
    k: key shape == (..., seq_len_k, depth)
    v: value shape == (..., seq_len_v, depth)
    mask: Float tensor with shape broadcastable 
          to (..., seq_len_q, seq_len_k). Defaults to None.

  Returns:
    output, attention_weights
  """

  matmul_qk = tf.matmul(q, k, transpose_b=True)  # (..., seq_len_q, seq_len_k)

  # scale matmul_qk
  dk = tf.cast(tf.shape(k)[-1], tf.float32)
  scaled_attention_logits = matmul_qk / tf.math.sqrt(dk)

  # add the mask to the scaled tensor.
  if mask is not None:
    scaled_attention_logits += (mask * -1e9)

  # softmax is normalized on the last axis (seq_len_k) so that the scores
  # add up to 1.
  attention_weights = tf.nn.softmax(
      scaled_attention_logits, axis=-1)  # (..., seq_len_q, seq_len_k)

  output = tf.matmul(attention_weights, v)  # (..., seq_len_v, depth)

  return output, attention_weights


class MultiHeadAttention(tf.keras.layers.Layer):

  def __init__(self, d_model, num_heads):
    super(MultiHeadAttention, self).__init__()
    self.num_heads = num_heads
    self.d_model = d_model

    assert d_model % self.num_heads == 0

    self.depth = d_model // self.num_heads

    self.wq = tf.keras.layers.Dense(d_model)
    self.wk = tf.keras.layers.Dense(d_model)
    self.wv = tf.keras.layers.Dense(d_model)

    self.dense = tf.keras.layers.Dense(d_model)

  def split_heads(self, x, batch_size):
    """Split the last dimension into (num_heads, depth).
    Transpose the result such that the shape is (batch_size, num_heads, seq_len, depth)
    """
    x = tf.reshape(x, (batch_size, -1, self.num_heads, self.depth))
    return tf.transpose(x, perm=[0, 2, 1, 3])

  def call(self, v, k, q, mask):
    batch_size = tf.shape(q)[0]

    q = self.wq(q)  # (batch_size, seq_len, d_model)
    k = self.wk(k)  # (batch_size, seq_len, d_model)
    v = self.wv(v)  # (batch_size, seq_len, d_model)

    q = self.split_heads(
        q, batch_size)  # (batch_size, num_heads, seq_len_q, depth)
    k = self.split_heads(
        k, batch_size)  # (batch_size, num_heads, seq_len_k, depth)
    v = self.split_heads(
        v, batch_size)  # (batch_size, num_heads, seq_len_v, depth)

    # scaled_attention.shape == (batch_size, num_heads, seq_len_v, depth)
    # attention_weights.shape == (batch_size, num_heads, seq_len_q, seq_len_k)
    scaled_attention, attention_weights = scaled_dot_product_attention(
        q, k, v, mask)

    scaled_attention = tf.transpose(
        scaled_attention, perm=[0, 2, 1,
                                3])  # (batch_size, seq_len_v, num_heads, depth)

    concat_attention = tf.reshape(
        scaled_attention,
        (batch_size, -1, self.d_model))  # (batch_size, seq_len_v, d_model)

    output = self.dense(concat_attention)  # (batch_size, seq_len_v, d_model)

    return output, attention_weights


def point_wise_feed_forward_network(d_model, dff):
  return tf.keras.Sequential([
      tf.keras.layers.Dense(dff,
                            activation='relu'),  # (batch_size, seq_len, dff)
      tf.keras.layers.Dense(d_model)  # (batch_size, seq_len, d_model)
  ])


class EncoderLayer(tf.keras.layers.Layer):

  def __init__(self, d_model, num_heads, dff, rate=0.1):
    super(EncoderLayer, self).__init__()

    self.mha = MultiHeadAttention(d_model, num_heads)
    self.ffn = point_wise_feed_forward_network(d_model, dff)

    self.layernorm1 = tf.keras.layers.experimental.LayerNormalization(
        epsilon=1e-6)
    self.layernorm2 = tf.keras.layers.experimental.LayerNormalization(
        epsilon=1e-6)

    self.dropout1 = tf.keras.layers.Dropout(rate)
    self.dropout2 = tf.keras.layers.Dropout(rate)

  def call(self, x, training, mask):
    # (batch_size, input_seq_len, d_model)
    attn_output, _ = self.mha(x, x, x, mask)
    attn_output = self.dropout1(attn_output, training=training)
    # (batch_size, input_seq_len, d_model)
    out1 = self.layernorm1(x + attn_output)

    # (batch_size, input_seq_len, d_model)
    ffn_output = self.ffn(out1)
    ffn_output = self.dropout2(ffn_output, training=training)
    # (batch_size, input_seq_len, d_model)
    out2 = self.layernorm2(out1 + ffn_output)

    return out2


class DecoderLayer(tf.keras.layers.Layer):

  def __init__(self, d_model, num_heads, dff, rate=0.1):
    super(DecoderLayer, self).__init__()

    self.mha1 = MultiHeadAttention(d_model, num_heads)
    self.mha2 = MultiHeadAttention(d_model, num_heads)

    self.ffn = point_wise_feed_forward_network(d_model, dff)

    self.layernorm1 = tf.keras.layers.experimental.LayerNormalization(
        epsilon=1e-6)
    self.layernorm2 = tf.keras.layers.experimental.LayerNormalization(
        epsilon=1e-6)
    self.layernorm3 = tf.keras.layers.experimental.LayerNormalization(
        epsilon=1e-6)

    self.dropout1 = tf.keras.layers.Dropout(rate)
    self.dropout2 = tf.keras.layers.Dropout(rate)
    self.dropout3 = tf.keras.layers.Dropout(rate)

  def call(self, x, enc_output, training, look_ahead_mask, padding_mask):
    # enc_output.shape == (batch_size, input_seq_len, d_model)

    # (batch_size, target_seq_len, d_model)
    attn1, attn_weights_block1 = self.mha1(x, x, x, look_ahead_mask)
    attn1 = self.dropout1(attn1, training=training)
    out1 = self.layernorm1(attn1 + x)

    # (batch_size, target_seq_len, d_model)
    attn2, attn_weights_block2 = self.mha2(enc_output, enc_output, out1,
                                           padding_mask)
    attn2 = self.dropout2(attn2, training=training)
    # (batch_size, target_seq_len, d_model)
    out2 = self.layernorm2(attn2 + out1)

    # (batch_size, target_seq_len, d_model)
    ffn_output = self.ffn(out2)
    ffn_output = self.dropout3(ffn_output, training=training)
    # (batch_size, target_seq_len, d_model)
    out3 = self.layernorm3(ffn_output + out2)

    return out3, attn_weights_block1, attn_weights_block2


class Encoder(tf.keras.layers.Layer):

  def __init__(self,
               num_layers,
               d_model,
               num_heads,
               dff,
               input_vocab_size,
               rate=0.1):
    super(Encoder, self).__init__()

    self.d_model = d_model
    self.num_layers = num_layers

    self.embedding = tf.keras.layers.Embedding(input_vocab_size, d_model)
    self.pos_encoding = positional_encoding(input_vocab_size, self.d_model)

    self.enc_layers = [
        EncoderLayer(d_model, num_heads, dff, rate) for _ in range(num_layers)
    ]

    self.dropout = tf.keras.layers.Dropout(rate)

  def call(self, x, training, mask):
    seq_len = tf.shape(x)[1]

    # adding embedding and position encoding.
    # (batch_size, input_seq_len, d_model)
    x = self.embedding(x)
    x *= tf.math.sqrt(tf.cast(self.d_model, tf.float32))
    x += self.pos_encoding[:, :seq_len, :]

    x = self.dropout(x, training=training)

    for i in range(self.num_layers):
      x = self.enc_layers[i](x, training, mask)

    # (batch_size, input_seq_len, d_model)
    return x


class Decoder(tf.keras.layers.Layer):

  def __init__(self,
               num_layers,
               d_model,
               num_heads,
               dff,
               target_vocab_size,
               rate=0.1):
    super(Decoder, self).__init__()

    self.d_model = d_model
    self.num_layers = num_layers

    self.embedding = tf.keras.layers.Embedding(target_vocab_size, d_model)
    self.pos_encoding = positional_encoding(target_vocab_size, self.d_model)

    self.dec_layers = [
        DecoderLayer(d_model, num_heads, dff, rate) for _ in range(num_layers)
    ]
    self.dropout = tf.keras.layers.Dropout(rate)

  def call(self, x, enc_output, training, look_ahead_mask, padding_mask):
    seq_len = tf.shape(x)[1]
    attention_weights = {}

    # (batch_size, target_seq_len, d_model)
    x = self.embedding(x)
    x *= tf.math.sqrt(tf.cast(self.d_model, tf.float32))
    x += self.pos_encoding[:, :seq_len, :]

    x = self.dropout(x, training=training)

    for i in range(self.num_layers):
      x, block1, block2 = self.dec_layers[i](x, enc_output, training,
                                             look_ahead_mask, padding_mask)

      attention_weights['decoder_layer{}_block1'.format(i + 1)] = block1
      attention_weights['decoder_layer{}_block2'.format(i + 1)] = block2

    # x.shape == (batch_size, target_seq_len, d_model)
    return x, attention_weights


class Transformer(tf.keras.Model):

  def __init__(self,
               num_layers,
               d_model,
               num_heads,
               dff,
               input_vocab_size,
               target_vocab_size,
               rate=0.1):
    super(Transformer, self).__init__()

    self.encoder = Encoder(num_layers, d_model, num_heads, dff,
                           input_vocab_size, rate)

    self.decoder = Decoder(num_layers, d_model, num_heads, dff,
                           target_vocab_size, rate)

    self.final_layer = tf.keras.layers.Dense(target_vocab_size)

  def call(self, inp, tar, training, enc_padding_mask, look_ahead_mask,
           dec_padding_mask):
    # (batch_size, inp_seq_len, d_model)
    enc_output = self.encoder(inp, training, enc_padding_mask)

    # dec_output.shape == (batch_size, tar_seq_len, d_model)
    dec_output, attention_weights = self.decoder(
        tar, enc_output, training, look_ahead_mask, dec_padding_mask)

    # (batch_size, tar_seq_len, target_vocab_size)
    final_output = self.final_layer(dec_output)

    return final_output, attention_weights


num_layers = 4
d_model = 128
dff = 512
num_heads = 8

input_vocab_size = vocab_inp_size
target_vocab_size = vocab_tar_size
dropout_rate = 0.1


class CustomSchedule(tf.keras.optimizers.schedules.LearningRateSchedule):

  def __init__(self, d_model, warmup_steps=4000):
    super(CustomSchedule, self).__init__()

    self.d_model = d_model
    self.d_model = tf.cast(self.d_model, tf.float32)

    self.warmup_steps = warmup_steps

  def __call__(self, step):
    arg1 = tf.math.rsqrt(step)
    arg2 = step * (self.warmup_steps**-1.5)

    return tf.math.rsqrt(self.d_model) * tf.math.minimum(arg1, arg2)


learning_rate = CustomSchedule(d_model)

optimizer = tf.keras.optimizers.Adam(
    learning_rate, beta_1=0.9, beta_2=0.98, epsilon=1e-9)

loss_object = tf.keras.losses.SparseCategoricalCrossentropy(from_logits=True)


def loss_function(real, pred):
  mask = tf.math.logical_not(tf.math.equal(real, 0))
  loss_ = loss_object(real, pred)

  mask = tf.cast(mask, dtype=loss_.dtype)
  loss_ *= mask

  return tf.reduce_mean(loss_)


train_loss = tf.keras.metrics.Mean(name='train_loss')
train_accuracy = tf.keras.metrics.SparseCategoricalAccuracy(
    name='train_accuracy')

transformer = Transformer(num_layers, d_model, num_heads, dff, input_vocab_size,
                          target_vocab_size, dropout_rate)


def create_masks(inp, tar):
  # Encoder padding mask
  enc_padding_mask = create_padding_mask(inp)

  # Used in the 2nd attention block in the decoder.
  # This padding mask is used to mask the encoder outputs.
  dec_padding_mask = create_padding_mask(inp)

  # Used in the 1st attention block in the decoder.
  # It is used to pad and mask future tokens in the input received by
  # the decoder.
  look_ahead_mask = create_look_ahead_mask(tf.shape(tar)[1])
  dec_target_padding_mask = create_padding_mask(tar)
  combined_mask = tf.maximum(dec_target_padding_mask, look_ahead_mask)

  return enc_padding_mask, combined_mask, dec_padding_mask


checkpoint_path = "./checkpoints/train"

ckpt = tf.train.Checkpoint(transformer=transformer, optimizer=optimizer)

ckpt_manager = tf.train.CheckpointManager(ckpt, checkpoint_path, max_to_keep=5)

# if a checkpoint exists, restore the latest checkpoint.
if ckpt_manager.latest_checkpoint:
  ckpt.restore(ckpt_manager.latest_checkpoint)
  print('Latest checkpoint restored!!')

EPOCHS = 20


@tf.function
def train_step(inp, tar):
  tar_inp = tar[:, :-1]
  tar_real = tar[:, 1:]

  enc_padding_mask, combined_mask, dec_padding_mask = create_masks(inp, tar_inp)

  with tf.GradientTape() as tape:
    predictions, _ = transformer(inp, tar_inp, True, enc_padding_mask,
                                 combined_mask, dec_padding_mask)
    loss = loss_function(tar_real, predictions)

  gradients = tape.gradient(loss, transformer.trainable_variables)
  optimizer.apply_gradients(zip(gradients, transformer.trainable_variables))

  train_loss(loss)
  train_accuracy(tar_real, predictions)


for epoch in range(EPOCHS):
  start = time.time()

  train_loss.reset_states()
  train_accuracy.reset_states()

  # inp -> portuguese, tar -> english
  for (batch, (inp, tar)) in enumerate(train_ds):
    train_step(inp, tar)

    if batch % 500 == 0:
      print('Epoch {} Batch {} Loss {:.4f} Accuracy {:.4f}'.format(
          epoch + 1, batch, train_loss.result(), train_accuracy.result()))

  if (epoch + 1) % 5 == 0:
    ckpt_save_path = ckpt_manager.save()
    print('Saving checkpoint for epoch {} at {}'.format(epoch + 1,
                                                        ckpt_save_path))

  print('Epoch {} Loss {:.4f} Accuracy {:.4f}'.format(epoch + 1,
                                                      train_loss.result(),
                                                      train_accuracy.result()))

  print('Time taken for 1 epoch: {} secs\n'.format(time.time() - start))

MAX_LENGTH = 40


def evaluate(inp_sentence):
  start_token = [input_tokenizer.vocab_size]
  end_token = [input_tokenizer.vocab_size + 1]

  # inp sentence is portuguese, hence adding the start and end token
  inp_sentence = start_token + input_tokenizer.encode(inp_sentence) + end_token
  encoder_input = tf.expand_dims(inp_sentence, 0)

  # as the target is english, the first word to the transformer should be the
  # english start token.
  decoder_input = [target_tokenizer.vocab_size]
  output = tf.expand_dims(decoder_input, 0)

  for i in range(MAX_LENGTH):
    enc_padding_mask, combined_mask, dec_padding_mask = create_masks(
        encoder_input, output)

    # predictions.shape == (batch_size, seq_len, vocab_size)
    predictions, attention_weights = transformer(
        encoder_input, output, False, enc_padding_mask, combined_mask,
        dec_padding_mask)

    # select the last word from the seq_len dimension
    predictions = predictions[:, -1:, :]  # (batch_size, 1, vocab_size)

    predicted_id = tf.cast(tf.argmax(predictions, axis=-1), tf.int32)

    # return the result if the predicted_id is equal to the end token
    if tf.equal(predicted_id, target_tokenizer.vocab_size + 1):
      return tf.squeeze(output, axis=0), attention_weights

    # concatentate the predicted_id to the output which is given to the decoder
    # as its input.
    output = tf.concat([output, predicted_id], axis=-1)

  return tf.squeeze(output, axis=0), attention_weights


def translate(sentence):
  result, attention_weights = evaluate(sentence)

  predicted_sentence = target_tokenizer.decode(
      [i for i in result if i < target_tokenizer.vocab_size])

  print('Input: {}'.format(sentence))
  print('Predicted translation: {}'.format(predicted_sentence))