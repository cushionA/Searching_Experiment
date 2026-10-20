# Efficient Crawling for Scalable Web Data Acquisition (experiment reproducibility kit)

This repository provides additional content (Appendix), code, and data supplementing the
research article *Efficient Crawling for Scalable Web Data Acquisition*.

## Additional content

[An extended version of the research paper](extended_version.pdf) presents the following additional material in its Appendix:
- The proof of Proposition 4
- Additional details about the websites’ characteristics
- The MIME type list defining the targets used in the experiments
- The initial keyword set provided to TRES
- A complete blocklist of URL extensions and MIME types
- The plots of the 8 websites that could not be included in Figure 4
- The exhaustive plots from the hyper-parameter studies
- Extra examples of typical tag paths
- Plots and inter-site confusion matrices for URL classifier models evaluation (exhaustive 88 confusion matrices can be found under [url_classifier_confusion_matrices](url_classifier_confusion_matrices/))
- Additional results on URL classification quality
- A visualization of the early-stopping mechanism for two websites
- A table summarizing the main characteristics of related focused crawlers
- An extended discussion of other crawler types, as well as alternatives to AUER for MAB algorithms

## Websites

Since intellectual property laws forbids us to redistribute content we are not author of, we cannot provide direct access to the replicated websites used in the paper to conduct experiments. These datasets can be provided upon request, however. The original websites are available from the following URLs:

- <https://www.abs.gov.au/>
- <https://www.assemblee-nationale.fr/>
- <https://www.bea.gov/>
- <https://www.census.gov/>
- <https://www.collectivites-locales.gouv.fr/>
- <https://www.cnis.fr/>
- <https://www.education.gouv.fr/>
- <https://www.ilo.org/>
- <https://www.interieur.gouv.fr/>
- <https://www.insee.fr/>
- <https://www.soumu.go.jp/>
- <https://www.justice.gouv.fr/>
- <https://nces.ed.gov/>
- <https://www.oecd.org/>
- <https://okfn.org/>
- <https://www.psa.gov.qa/>
- <https://www.who.int/>
- <https://www.worldbank.org/>

## Code

The source code of two types of crawlers are made available under the [MIT License](LICENSE).

### Local Crawlers

You will find in the [local_crawlers](code/local_crawlers/) folder, the code we used to conduct crawling experiments locally. Specifically:

* [data](code/local_crawlers/data/) is the place where locally replicated websites in the form of a `.db` file should be found.
* [crawlers](code/local_crawlers/crawlers/) contains the code of the different crawlers we present in the paper: our approach as well as baselines (one subdirectory for each crawler). [generic_local_crawler.py](code/local_crawlers/crawlers/generic_local_crawler.py) contains the local crawling framework used for all different crawlers, and [rl_actions.py](code/local_crawlers/crawlers/rl_actions.py) manages the actions when reinforcement learning is used.
* [crawl.sh](code/local_crawlers/crawl.sh) is a script that runs crawls by choosing a crawler, its parameters, etc.
* [graphical_results](code/local_crawlers/graphical_results/) contains the code used to generate plots from crawls runs, exactly as in the paper.
* [generate_plot.sh](code/local_crawlers/generate_plot.sh) is a script that generates plots by specifying where are the crawl results, which crawlers you want to integrate, etc.

### On-Line to Local Crawler

In [online_to_local_crawler](code/online_to_local_crawler), you will find a script [online_to_local_crawling.sh](code/online_to_local_crawler/online_to_local_crawling.sh) that can be used to make local replications of websites, to further conduct local crawling experiments, as presented above. The replications as stored are SQLite3 [^1] relational databases in the [data](code/local_crawlers/data/) folder. 

### Semi-Online Crawlers

In [semi_online_crawlers](code/semi_online_crawlers) you will find the code we used to conduct the "semi-online" experiments, i.e., first checking if the wanted resource is on a local database, and otherwise crawling and storing it locally. Specifically:
* [data](code/semi_online_crawlers/data/) is the place where are stored the local databases as `.db` files (in which we look for and add resources during the crawls)
* [crawlers](code/semi_online_crawlers/crawlers/) contains the code of our crawler and the different baselines, leveraging the Scrapy [^2] framework. 
* [crawl.sh](code/semi_online_crawlers/crawl.sh) is a script that runs crawls by choosing a crawler and specifying its parameters.
* [graphical_results](code/semi_online_crawlers/graphical_results/) contains the code used to generate plots from crawls runs, exactly as in the paper.
* [generate_plot.sh](code/semi_online_crawlers/generate_plot.sh) is a script that generates plots by specifying where are the crawl results, which crawlers you want to integrate, etc.

[^1]: https://docs.python.org/3/library/sqlite3.html
[^2]: https://pypi.org/project/Scrapy/
