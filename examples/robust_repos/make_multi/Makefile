all: beta alpha

alpha:
	printf '%s\n' '#!/usr/bin/env sh' 'printf "alpha\n"' > alpha
	chmod +x alpha

beta:
	printf '%s\n' '#!/usr/bin/env sh' 'printf "beta\n"' > beta
	chmod +x beta

clean:
	rm -f alpha beta

.PHONY: all clean

