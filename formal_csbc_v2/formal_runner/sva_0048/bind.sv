module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire edn_done,
    input wire clk_i,
    input wire reset
);

assert property (@(posedge clk_i) disable iff (reset) (1) |-> $stable(edn_done));

endmodule


bind keymgr_reseed_ctrl sva_checker sva_checker_inst (.*);
