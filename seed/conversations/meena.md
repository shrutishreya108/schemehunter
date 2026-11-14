# Rehearsal script: Meena, live onboarding (≈90 seconds)

Persona: Meena Kumari, 35, Bengaluru (urban), OBC, 10th pass, wants a ₹5 lakh tailoring unit.
Seeded facts already cover name, age, category, sector, stage, project cost, area, education.
The demo shows the **NEEDS_INFO → next question** loop for the loan schemes.

| # | Who | Hindi (spoken on stage) | English gloss |
|---|-----|------------------------|---------------|
| 1 | Agent | नमस्ते मीना जी! आप कितने रुपये का लोन लेना चाहती हैं? | Hello Meena! How much loan do you want? |
| 2 | Meena | तीन लाख रुपये। | Three lakh rupees. |
| 3 | Agent | ठीक है। आपके परिवार की सालाना आमदनी लगभग कितनी है? | OK. Roughly what is your family's yearly income? |
| 4 | Meena | करीब एक लाख बीस हज़ार। | About 1.2 lakh. |
| 5 | Agent | क्या आप विधवा हैं या दिव्यांग? (सिर्फ़ योजना के नियम के लिए पूछ रही हूँ) | Are you a widow or a person with a disability? (asked only for the scheme rule) |
| 6 | Meena | नहीं। | No. |
| 7 | Agent | आप इन योजनाओं के लिए आवेदन कर सकती हैं: PMEGP, मुद्रा (किशोर), उद्योगिनी। हर एक के साथ नियम का पन्ना देखें। | You qualify to apply for PMEGP, Mudra (Kishore), Udyogini. See the rule page for each. |

Expected engine state after turn 6 (facts: loan_amount 300000, family_income 120000, special_circumstance none):
PMEGP FIT · Mudra FIT (Kishore) · Udyogini FIT · Stand-Up India NO_FIT (loan_range) · CGTMSE NEEDS_INFO (enterprise_size).

Never say "you will get" or "approved". Strongest allowed: "you qualify to apply."
