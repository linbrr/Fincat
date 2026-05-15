"""QueryPreprocessor — query normalization for memory retrieval.

Cleans user queries before embedding: removes stopwords, filler words,
and normalizes synonymous expressions for better vector matching.
"""

from __future__ import annotations

import re

# Chinese stopwords (common function words that add noise to embeddings)
_STOPWORDS = {
    "的", "了", "吗", "呢", "吧", "啊", "呀", "哦", "嗯", "额",
    "是", "在", "有", "和", "与", "或", "但", "而", "就", "都",
    "把", "被", "给", "让", "向", "从", "到", "对", "为", "以",
    "这", "那", "这个", "那个", "这些", "那些", "什么", "怎么",
    "一个", "一些", "一点", "一下", "一起", "一般", "一样",
    "我", "你", "他", "她", "它", "我们", "你们", "他们",
    "想", "要", "会", "能", "可以", "应该", "需要", "得",
    "很", "非常", "特别", "比较", "挺", "太", "真", "好",
}

# Filler words / interjections to strip
_FILLER_PATTERN = re.compile(
    r"(嗯|哦|额|啊|呀|哈|嘿|喂|哎|唉|嗨|呵|呵|嘻嘻|哈哈|嘿嘿|呃)"
)

# Synonym normalization map
_SYNONYMS = {
    "帮我看看": "查看",
    "帮我查查": "查询",
    "帮我搜搜": "搜索",
    "帮我找找": "查找",
    "帮我分析一下": "分析",
    "帮我推荐": "推荐",
    "帮我看看": "查看",
    "查一下": "查询",
    "看一看": "查看",
    "说一下": "说明",
    "讲一下": "说明",
    "介绍一下": "介绍",
    "了解一下": "了解",
}

# Compile synonym pattern for efficient matching
_SYNONYM_RE = re.compile("|".join(re.escape(k) for k in sorted(_SYNONYMS, key=len, reverse=True)))


class QueryPreprocessor:
    """Clean and normalize user queries for better embedding quality."""

    def preprocess(self, query: str) -> str:
        """Full preprocessing pipeline: clean → normalize → strip stopwords."""
        text = query.strip()
        if not text:
            return ""

        # 1. Remove filler words
        text = _FILLER_PATTERN.sub("", text)

        # 2. Normalize synonyms (longest match first)
        text = _SYNONYM_RE.sub(lambda m: _SYNONYMS[m.group()], text)

        # 3. Remove stopwords (only standalone, not part of longer words)
        # Split by common delimiters, filter stopwords, rejoin
        words = self._tokenize(text)
        words = [w for w in words if w not in _STOPWORDS or len(words) <= 2]

        result = "".join(words)
        # Collapse multiple spaces/punctuation
        result = re.sub(r"\s+", " ", result).strip()
        return result if len(result) >= 2 else query.strip()

    def normalize(self, query: str) -> str:
        """Lightweight normalization (no stopword removal)."""
        text = query.strip()
        text = _FILLER_PATTERN.sub("", text)
        text = _SYNONYM_RE.sub(lambda m: _SYNONYMS[m.group()], text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Simple character/word tokenization for Chinese text."""
        tokens = []
        current = ""
        for ch in text:
            if "一" <= ch <= "鿿":
                # Chinese character
                if current:
                    tokens.append(current)
                    current = ""
                tokens.append(ch)
            elif ch.isalnum():
                current += ch
            else:
                if current:
                    tokens.append(current)
                    current = ""
        if current:
            tokens.append(current)
        return tokens
