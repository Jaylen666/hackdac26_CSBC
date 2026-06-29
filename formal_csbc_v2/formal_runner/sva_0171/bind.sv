module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire state_q,
    input wire kmac_data_i,
    input wire strb,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (state_q == StTxLast && !kmac_data_i.ready) |-> $stable(strb));

endmodule


bind keymgr_kmac_if sva_checker sva_checker_inst (.*);
