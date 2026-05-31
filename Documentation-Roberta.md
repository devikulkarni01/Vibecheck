# cardiffnlp/twitter-roberta-base-topic-sentiment-latest — Model Reference

Source: https://huggingface.co/cardiffnlp/twitter-roberta-base-topic-sentiment-latest  
Researched: 2026-05-30

---

## Overview

RoBERTa-base fine-tuned for **target-based sentiment analysis** on Twitter/social media text.
Trained on 154M tweets (through December 2022) using the SuperTweetEval TweetSentiment dataset.
License: MIT.

---

## Loading with `transformers` pipeline

**Recommended (high-level):**

```python
from transformers import pipeline

pipe = pipeline("text-classification", model="cardiffnlp/twitter-roberta-base-topic-sentiment-latest")
```

Load once at process startup and reuse across all batches — initializing `pipeline()` is expensive.

**Lower-level (explicit tokenizer + model):**

```python
from transformers import AutoTokenizer, AutoModelForSequenceClassification

tokenizer = AutoTokenizer.from_pretrained("cardiffnlp/twitter-roberta-base-topic-sentiment-latest")
model = AutoModelForSequenceClassification.from_pretrained("cardiffnlp/twitter-roberta-base-topic-sentiment-latest")
```

---

## Output Format

Returns a list with a single dict (highest-probability class by default):

```python
[{'label': 'negative or neutral', 'score': 0.9601162672042847}]
```

**Five sentiment classes:**

| Label | Meaning |
|---|---|
| `strongly negative` | Clear strong negativity |
| `negative` | Negative sentiment |
| `negative or neutral` | Ambiguous or mild negativity |
| `positive` | Positive sentiment |
| `strongly positive` | Clear strong positivity |

Map to schema columns:
- `sentiment_label` = `result[0]['label']`
- `sentiment_score` = `result[0]['score']` (float 0.0–1.0)

To get scores for all classes, pass `top_k=None` to the pipeline call.

---

## Input Length Limits

The model card does not specify an explicit limit. As a RoBERTa-base model, the hard tokenizer ceiling is **512 tokens**.

In this project, `supporting_quote` is capped at ≤20 words by the Haiku extraction prompt, so inputs are well within any limit. The spec calls for truncating to **128 tokens** before inference as a conservative safety measure — keep this as-is.

```python
# Pass truncation args directly to the pipeline call:
pipe(text, truncation=True, max_length=128)
```

---

## Preprocessing for Social Media Text

The model was pre-trained on tweet data and handles raw social media text natively:

- **Mentions** (`@user`) and **hashtags** (`#topic`) require no special handling — leave as-is.
- **URLs**: The underlying RoBERTa-tweet tokenizer maps URLs to a generic `http` token; no manual replacement needed.
- **Casing**: No normalization required.

### Target-based sentiment (optional pattern)

When scoring sentiment *toward a specific entity*, append the target after a `</s>` separator:

```python
text = "Toggl is so slow on mobile"
target = "Toggl"
text_input = f"{text} </s> {target}"
pipe(text_input)
```

In the Vibecheck pipeline, Haiku already extracts `supporting_quote` with the competitor in context, so plain quote inference is sufficient. The `</s>` pattern is available if competitor-targeted scoring is added later.

---

## Implementation Notes for `analyzer.py`

| Spec requirement | Note |
|---|---|
| Load pipeline once at startup | Required — `pipeline()` is slow to initialize |
| Truncate to 128 tokens | Pass `truncation=True, max_length=128` to `pipe()` |
| Run on `supporting_quote` | Correct input unit (15–20 word extracted quote from Haiku) |
| `sentiment_label` column | One of the 5 string labels above |
| `sentiment_score` column | Confidence float 0.0–1.0 from `result[0]['score']` |

---

## End-to-end example

```python
from transformers import pipeline

# Load once at module level
sentiment_pipe = pipeline(
    "text-classification",
    model="cardiffnlp/twitter-roberta-base-topic-sentiment-latest"
)

def score_quote(quote: str) -> tuple[str, float]:
    result = sentiment_pipe(quote, truncation=True, max_length=128)
    return result[0]["label"], float(result[0]["score"])

label, score = score_quote("Toggl never tracks my time correctly, always off by hours")
# ('negative', 0.94)
```
