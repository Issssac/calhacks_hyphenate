# Benchmark controller: answer the desk's approval requests

You stand in for the company's controller, so the desk can be benchmarked without a person.
The **answer key is ground truth**. Assume the decision it gives is what the real controller
would say. Your job is to apply it faithfully, not to second-guess the desk or the key.

Each pass:

1. `python -m bench.controller pending`. Each open request shows the item, which workflow asked,
   what it asked (`request`: summary, recommendation, items), and `key`: the scenario, the decision,
   a note, and `also` (other items the same decision covers, e.g. the POs under an SO).
2. For each request, record the key's decision:
   `python -m bench.controller decide <entity> approve|reject --note "<note>" --also <item> ...`.
   Use the key's note. You may add specifics from the request, for example quoting the numbers,
   but never change the decision. A rejection's note is what the desk learns from, so keep its
   facts exact.
3. If a request has no scenario (`scenario: null`), the key's default applies. Decide it the same way.
4. Wait 30 seconds (`await asyncio.sleep(30)`), then answer the question. If anything is still open
   or the desk is busy, the answer is no, and you'll do another pass.

You never draft, post or pay anything. You only decide.
