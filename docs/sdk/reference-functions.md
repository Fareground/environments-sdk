# functions

## Functions

Every function by group. Read one group's signatures and docs with `guide('functions.<group>')`.

Core (the start page teaches them): $count $sum $avg $min $max $filter $map $dict $top $sort $best $any $all $len $get $entity $records $round $floor $clamp $chance $randint $normal $choice. Every other function is extended.

General functions, for any contract:

- `collections` (counting, summing, ranking and filtering lists and entity types): $all $any $avg $best $coalesce $count $dict $filter $first $flatten $get $is $keys $last $len $map $max $median $min $mode $pick $quantile $range $reverse $slice $sort $stdev $sum $tally $top $unique $values
- `world` (entities, records, events and what agents were shown): $asset $entity $events $records $seen
- `math` (arithmetic, trigonometry, interpolation): $abs $acos $asin $atan $atan2 $ceil $clamp $comb $cos $erf $exp $factorial $floor $gcd $interp $lcm $log $logit $logsumexp $pct $pi $round $sigmoid $sign $sin $softmax $sqrt $tan $tanh
- `random` (seeded draws and distributions): $beta $binomial $chance $choice $dice $dirichlet $exponential $gamma $geometric $lognormal $multinomial $mvnormal $normal $poisson $randint $sample $shuffle $triangular $truncnormal $uniform $weibull $zipf
- `text` (text and formatting): $chars $contains $ends_with $fmt $join $lower $matches $pad $replace $similar $split $starts_with $substr $text $title $trim $upper $words
- `dates` (calendar arithmetic and parts of ISO dates): $date_add $date_part $days_between $is_holiday
- `lists` (list and map manipulation, sets): $chunk $cumsum $diff $difference $index $insert $intersect $items $lookup $lookup_one $merge $pick_keys $rank $remove_at $rotate $set_at $union $window $without $zip
- `stats` (statistics, time series and forecast scores): $abs_error $autocorr $brier $corr $cov $crps $drawdown $elo $ema $entropy $excess_kurtosis $gini $hhi $histogram $linreg $log_loss $market_realism $market_stats $percentile_rank $pool $returns $sma $tally_votes $variance $zscore
- `space` (grids, graphs, networks and links): $at $cells $clustering $components $degree $distance $empty $hops $layer $link $linked $links $near $nearest $neighbors $normal_for $path_distance $random_empty $random_for $relation
- `words` (word games and puzzles: dictionaries, anagrams, crosswords, sudoku): $anagram $mask $puzzle $solve $wordle_feedback

A mechanism's functions read its state (a board, a deck, a market …) and can be called only in a contract that declares a mechanism of its family; each family's page lists them: `guide('market')`, `guide('economy')`, `guide('agreements')`, `guide('decision')`, `guide('game')`, `guide('groups')`, `guide('social')`, `guide('host')`, `guide('pattern')`.
