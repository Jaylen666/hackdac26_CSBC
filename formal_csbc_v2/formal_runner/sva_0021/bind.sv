module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire Shares,
    input wire key_q,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (Shares == '0) |=> (key_q == '0));

endmodule


bind keymgr_sideload_key sva_checker sva_checker_inst (.*);
