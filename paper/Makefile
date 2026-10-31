# Build the Synthetic Council paper.
# Usage: make (or make clean)

MAIN = main
TEXFLAGS = -interaction=nonstopmode -halt-on-error

.PHONY: all clean figures

all: $(MAIN).pdf

$(MAIN).pdf: $(MAIN).tex refs.bib figures/fig1_pipeline.tex figures/fig2_arm_delta.pdf figures/fig3_cohesion.pdf
	pdflatex $(TEXFLAGS) $(MAIN).tex
	biber $(MAIN)
	pdflatex $(TEXFLAGS) $(MAIN).tex
	pdflatex $(TEXFLAGS) $(MAIN).tex

figures:
	uv run python ../scripts/make_paper_figures.py

clean:
	rm -f $(MAIN).aux $(MAIN).bbl $(MAIN).bcf $(MAIN).blg $(MAIN).log $(MAIN).out $(MAIN).run.xml $(MAIN).pdf
