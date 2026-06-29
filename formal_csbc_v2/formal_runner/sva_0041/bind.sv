module bind_wrapper;
    // This module exists solely to give sby a top-level entry point.
    // The actual binding happens via the bind statement below.
endmodule

module sva_checker (
    input wire enables_sub,
    input wire enables_d,
    input wire clk_i,
    input wire rst_ni
);

assert property (@(posedge clk_i) disable iff (!rst_ni) (enables_sub == enables_d - 1) |-> $onehot0(enables_d & enables_sub));

endmodule


bind keymgr_kmac_if sva_checker sva_checker_inst (.*);
