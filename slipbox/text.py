"""Query tokenisation shared by the structural and lexical layers.

Both `indexmd.match` (topic overlap, lookup step 1) and `lookup._probe_scorer`'s
fallback (token overlap, step 2) reduce text to a bag of words and score by how
much of the query it covers. They had a private copy of the same tokeniser each,
and both kept every word longer than two characters — so a query matched a topic
on **"and"**, and one function word was enough to nominate the entire store.

That is not a cosmetic problem: a spurious structural hit puts a note through the
positional layer, which marks it as found by two layers, which used to outrank a
genuinely near vector neighbour. Filtering function words is what keeps the
structural layer's precision from collapsing to "everything".
"""
from __future__ import annotations

import re

_WORD_RE = re.compile(r"\w+", re.UNICODE)

_MIN_LENGTH = 3

# English function words of three characters or more (shorter ones are already
# dropped by `_MIN_LENGTH`). Deliberately closed-class only — articles,
# pronouns, auxiliaries, conjunctions, prepositions and interrogatives. No
# content word belongs here: dropping a noun would make a topic unfindable,
# which is a worse failure than one spurious match.
STOPWORDS = frozenset("""
    about above after again against all almost along already also although always
    among and any anyone anything are around because been before behind being
    below beneath beside besides between beyond both but can cannot could did
    does doing done down due during each either else enough etc even ever every
    everything few for from further get gets got had has have having her here
    hers herself him himself his how however into its itself just least less let
    like made make many may maybe might mine more most much must myself near
    need neither never new next nor not nothing now off often once one only onto
    other others ought our ours ourselves out over own per perhaps please put
    quite rather really same say says see seem seen several shall she should
    since some someone something soon still such take than that the their theirs
    them themselves then there therefore these they thing things this those
    though through thus together too toward towards under until upon use used
    uses using very via was way well were what when whenever where whereas
    whether which while who whom whose why will with within without would yet
    you your yours yourself
""".split())


def tokens(text: str, *, drop_stopwords: bool = True) -> set[str]:
    """Lower-cased content words of `text`, function words removed.

    `drop_stopwords=False` keeps them — used where the caller is matching a
    note's own prose rather than scoring a query against it.
    """
    found = {t.lower() for t in _WORD_RE.findall(text or "") if len(t) >= _MIN_LENGTH}
    return found - STOPWORDS if drop_stopwords else found
