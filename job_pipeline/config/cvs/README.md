These three files are pre-filled with the text of your existing category
CVs from your CV folder:

- `ai_transformation_consultant.md` — from `Hamideh_Ahooei_CV_AI_Transformation_Consultant.docx`
- `technical_business_analyst.md` — from `Hamideh_Ahooei_CV_Technical_Business_Analyst.docx`
- `implementation_fde.md` — from `Hamideh_Ahooei_CV_Implementation_FDE.docx`

The scorer and answer-drafter read these as your real, documented
experience — this is what keeps "leadership" on your resume from
satisfying "8 years of Engineering Management," and what keeps drafted
answers from inventing anything.

If you update one of the underlying .docx files, re-paste its text into the
matching .md file here so scoring/tailoring/answers stay current — nothing
re-syncs these automatically.

(Tailoring itself, in `pipeline/tailoring.py`, reads the actual .docx files
directly from your CV folder — these .md copies are only for scoring and
answer-drafting, which need plain text.)
