# functions

## Functions

Every function by group. Read one group's signatures and docs with `guide('functions.<group>')`.

Core functions, for any contract:

- `collections` (counting, summing, ranking and filtering lists and entity types): $all $any $avg $bottom $count $dict $filter $first $flatten $get $ids $is $keys $last $len $map $max $median $min $mode $pick $quantile $range $reverse $slice $sort $stdev $sum $tally $top $unique $values
- `world` (entities, records, events and what agents were shown): $asset $entity $events $exists $pattern_values $records $seen
- `math` (arithmetic, trigonometry, interpolation, linear algebra): $abs $acos $asin $atan $atan2 $ceil $clamp $comb $cos $det $dot $e $erf $exp $factorial $floor $gcd $hypot $identity $interp $inverse $lcm $lerp $linsolve $log $log_base $logit $logsumexp $matmul $mvnormal $pct $pi $pow $round $sigmoid $sign $sin $softmax $sqrt $tan $tanh $transpose
- `random` (seeded draws and distributions): $beta $binomial $chance $choice $dice $dirichlet $exponential $gamma $geometric $lognormal $multinomial $normal $poisson $randint $random $sample $shuffle $triangular $truncnormal $uniform $weibull $zipf
- `text` (text and formatting): $char_at $chars $contains $count_text $ends_with $fmt $index_of $join $lower $matches $pad $repeat_text $replace $similar $split $starts_with $substr $text $title $trim $upper $words
- `dates` (calendar arithmetic and parts of ISO dates): $date_add $date_part $days_between $is_holiday
- `lists` (list and map manipulation, sets): $argmax $argmin $chunk $count_of $cumsum $diff $difference $enumerate $flatten_deep $index $insert $intersect $is_subset $items $lookup $lookup_one $merge $pick_keys $rank $remove_at $rotate $set_at $union $window $without $zip
- `stats` (statistics, time series and forecast scores): $abs_error $autocorr $brier $corr $cov $crps $drawdown $elo $ema $entropy $gini $hhi $histogram $linreg $log_loss $percentile_rank $pool $returns $sma $variance $zscore
- `space` (grids, graphs, networks and links): $at $cells $chance_for $clustering $components $degree $distance $empty $hops $layer $link $linked $links $near $nearest $neighbors $normal_for $random_empty $random_for $relation

Mechanism functions, for reading a mechanism family's state (a board, a deck, a market …); each group is also on its family's page:

- `market` (Trading venues: continuous order books, auctions and procurement tenders, prediction markets and posted-price shops): $amm $amm_cost $amm_ok $amm_outcomes $auction $auction_ok $auction_text $book $book_account $book_depth $book_ok $book_orders $book_rules $cpmm_prices $excess_kurtosis $lmsr_cost $lmsr_prices $market_realism $market_stats $package_winners $posted_counters $posted_line $posted_ok $posted_price $realized_vol $shelf $vol_clustering $volume_vol_corr
- `economy` (Money, goods and making things: ledgers (currencies, taxes, loans), inventories, production, supply chains, customers' demand for stocked items and the policies that replenish them): $conserved $count_items $demand_totals $ground_items $has $items_text $loose_total $max_batches $net_worth $owned_items $pipeline $pipeline_text $recipes $recipes_text $replenishment_totals $skill $space_left $stock_conserved $total_held
- `agreements` (Commitments between agents over time: negotiated deals, jobs, subscriptions and bookings): $booking_text $places_text $subscribed $terms_text
- `decision` (Collective choice: ballots and structured deliberation with motions and votes): $decisions $discussion_over $house $pending_motion $tally_votes
- `game` (Game equipment: boards with enforced rules, cards, betting pots and worker-placement slots): $anagram $blackjack_soft $blackjack_value $board_at $board_cell $board_in_check $board_line $board_moves $board_render $board_score $card_names $card_visible $cards_table $claims $follow_suit $hand $mask $open_spaces $poker_hand $poker_rank $pot_live $pot_options $pot_table $pot_total $puzzle $runs $sets $slot_board $solve $top_card $top_cards $trick_winner $wordle_feedback $zone
- `flow` (Who acts when and how it ends: turn order, procedures with phases, victory conditions): $best $stack $turn_order $turn_rank $won
- `groups` (Who belongs with whom: hidden roles and teams, factions and alliances, relationships, stable matching): $allies $faction_of $factions $joinable $known_role $team_alive $teammates
- `social` (Talking and spreading: channels (rooms, direct messages), a social feed, diffusion over a network): $adopters $channel_log $channels $exposures $feed $followers $following $groups $heard $homophily $inbox $inbox_channels $influence $insularity $invites $reach $recent_messages $spread_state $trending $unread
- `mind` (What agents know and remember: beliefs with confidence, memory with recall, generated personas): $belief $beliefs_of $believes $confidence $memories
- `conditions` (Effects on entities over time: statuses, cooldowns, channeled actions and terrain): $ability_text $can_enter $channeling $charges $cooldown_left $effective $has_status $ready $status_rounds $status_stacks $status_text $terrain
